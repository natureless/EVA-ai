// Exercise the shipped browser handlers without a browser or a running EVA.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function chatHarness(fetch, overrides = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../../ui/web/static/chat.js"), "utf8");
  const context = {
    fetch, TextDecoder, AbortController,
    setTimeout: (fn, ms) => { const timer = setTimeout(fn, ms); timer.unref(); return timer; },
    clearTimeout, ParticleController: { burstParticles() {}, _state: "idle" }, ...overrides,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../ui/web/static/http.js"), "utf8"), context);
  vm.runInNewContext(source.slice(0, source.indexOf("// ── Init")) + "\nthis.chat = Chat;", context);
  const chat = context.chat;
  chat._realRender = chat._renderMessages;
  chat._renderMessages = () => {
    const message = chat._messages.at(-1);
    chat.rendered = message?.text || (message && chat._noticeText(message));
  };
  return chat;
}

test("SSE named receipt across split chunks displays reply, never metadata JSON", async () => {
  const reply = "第一行\n第二行";
  const wire = `data: 第一行\\n第二行\n\nevent: result\ndata: ${JSON.stringify({reply, completed: true, ok: false, terminal_state: "outcome_unknown"})}\n\ndata: [DONE]\n\n`;
  const bytes = Buffer.from(wire);
  let index = 0;
  const chat = chatHarness(async () => ({
    ok: true, body: { getReader: () => ({ read: async () => {
      if (index >= bytes.length) return { done: true };
      const value = bytes.subarray(index, index + 7);
      index += value.length;
      return { done: false, value };
    } }) },
  }));
  await chat._sendStream("hello");
  assert.equal(chat.rendered, reply);
  assert.equal(chat._streamAbort, null);
});

test("SSE receipt metadata stays attached to the assistant message", async () => {
  const chat = chatHarness(async () => streamReply("已完成核对", {mode: "deep", strategy: "native"}, "deep"));
  await chat._sendStream("请核对", false, 0, "deep");
  const message = chat._messages[chat._messages.length - 1];
  assert.equal(message.mode, "deep");
  assert.deepEqual(JSON.parse(JSON.stringify(message.mode_info)), {mode: "deep", strategy: "native"});
});

test("SSE HTTP rejection is shown instead of reading an absent stream", async () => {
  const chat = chatHarness(async () => ({ ok: false, json: async () => ({accepted: false, detail: "Queue full"}) }));
  await chat._sendStream("hello");
  assert.match(chat.rendered, /Queue full/);
  assert.equal(chat._messages[0].status, 'rejected');
});

test("Immediate polling recovers a reply delivered before WS registration", async () => {
  const calls = [];
  const chat = chatHarness(async url => {
    calls.push(url);
    return { ok: true, status: 200, json: async () => url === "/api/chat"
      ? {accepted: true, task_id: "fast", request_deadline_sec: 1}
      : {completed: true, task_id: "fast", reply: "Retained answer"} };
  });
  await chat._sendViaWS("hello");
  assert.deepEqual(calls, ["/api/chat", "/api/chat/result/fast"]);
  assert.equal(chat._messages[0].text, "Retained answer");
  assert.equal(Object.keys(chat._pendingTasks).length, 0);
});

test("A missing receipt ends local waiting without claiming action failure", async () => {
  const chat = chatHarness(async url => ({
    ok: url === "/api/chat", status: url === "/api/chat" ? 200 : 404,
    json: async () => url === "/api/chat" ? {accepted: true, task_id: "missing"} : {error: "result_unknown_or_expired"},
  }));
  await chat._sendViaWS("hello");
  assert.match(chat._noticeText(chat._messages[0]), /无法确定结果/);
  assert.equal(chat._messages[0].taskId, 'missing');
  assert.equal(chat._messages[0].status, 'unknown');
  assert.equal(Object.keys(chat._pendingTasks).length, 0);
});

test("recovered summary without reply stays explicit and never repeats the request", async () => {
  const calls = [];
  const chat = chatHarness(async (url, options) => {
    calls.push({url, method: options.method || "GET"});
    return {ok: true, status: 200, json: async () => url === "/api/chat"
      ? {accepted: true, task_id: "recovered"}
      : {completed: true, task_id: "recovered", ok: true, terminal_state: "succeeded",
        reply: "", reply_available: false, error: "reply_not_retained", review: {passed: null}}};
  });
  await chat._sendViaWS("original request");
  const message = chat._messages[0];
  assert.equal(message.status, "completed");
  assert.equal(message.taskId, "recovered");
  assert.equal(message.text, "");
  assert.equal(message.notice, "reply_missing");
  assert.match(chat._noticeText(message), /正文未保存/);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(Object.keys(chat._pendingTasks).length, 0);
});

test("Repeated failed WebSocket reconnects keep scheduling, then recover", () => {
  const sockets = [], timers = [];
  const chat = chatHarness(async () => {}, {
    location: { protocol: 'https:', host: 'eva.example.com' },
    document: { getElementById: () => null },
    WebSocket: class { constructor(url) { this.url = url; sockets.push(this); } },
    setTimeout: (callback, delay) => { timers.push({callback, delay}); return timers.length; },
  });
  chat._connectWS();
  for (let i = 0; i < 3; i++) {
    sockets[i].onclose({code: 1006});
    assert.equal(timers.length, i + 1);
    timers[i].callback();
    assert.equal(sockets.length, i + 2);
  }
  assert.ok(timers[2].delay >= 4000);
  sockets[3].onopen();
  assert.equal(chat._wsReconnectAttempts, 0);
  assert.equal(chat._wsReconnectTimer, null);
  sockets[0].onclose({code: 1006});
  assert.equal(timers.length, 3, 'an obsolete socket must not schedule another connection');
});

test("Expired login during receipt polling preserves uncertainty and never resubmits", async () => {
  const calls = [];
  const chat = chatHarness(async (url, options) => {
    calls.push({url, method: options.method || 'GET'});
    return url === '/api/chat'
      ? {ok: true, status: 200, json: async () => ({accepted: true, task_id: 'already-admitted'})}
      : {type: 'opaqueredirect', status: 0, ok: false};
  });
  await chat._sendViaWS('only once');
  assert.equal(calls.filter(x => x.method === 'POST').length, 1);
  assert.equal(calls.length, 2);
  assert.match(chat._noticeText(chat._messages[0]), /可能仍在执行/);
  assert.equal(chat._messages[0].taskId, 'already-admitted');
  assert.equal(chat._messages[0].status, 'unknown');
  assert.equal(Object.keys(chat._pendingTasks).length, 0);
});

test("SSE login redirect is not parsed as streamed output", async () => {
  let reads = 0;
  const chat = chatHarness(async () => ({type: 'opaqueredirect', status: 0,
    body: {getReader() { reads++; throw new Error('must not read login HTML'); }}}));
  await chat._sendStream('only once');
  assert.equal(reads, 0);
  assert.match(chat.rendered, /重新登录/);
});

function streamReply(reply, modeInfo = null, mode = null) {
  const wire = Buffer.from(`event: result\ndata: ${JSON.stringify({ reply, completed: true,
    ...(modeInfo ? {mode_info: modeInfo} : {}), ...(mode ? {mode} : {}) })}\n\ndata: [DONE]\n\n`);
  let delivered = false;
  return { ok: true, body: { getReader: () => ({ read: async () => {
    if (delivered) return { done: true };
    delivered = true;
    return { done: false, value: wire };
  } }) } };
}

function formHarness(fetch) {
  const element = () => ({ events: {}, style: {}, addEventListener(type, handler) { this.events[type] = handler; } });
  const ids = { chatForm: element(), chatInput: element(), sendBtn: element(),
    streamToggle: { checked: true }, rememberToggle: { checked: false }, cubeModel: {} };
  const timers = new Map();
  let sequence = 0, responding = 0, idle = 0, selectedMode = 'normal';
  const startedModes = [];
  const chat = chatHarness(fetch, {
    document: { getElementById: id => ids[id] },
    ParticleController: {
      setResponding(mode) { responding++; startedModes.push(mode); },
      getConversationMode() { return selectedMode; },
      _setIdle() { idle++; }, burstParticles() {},
    },
    setTimeout(callback, delay) {
      const id = ++sequence;
      timers.set(id, { callback, delay, cancelled: false });
      return id;
    },
    clearTimeout(id) { if (timers.has(id)) timers.get(id).cancelled = true; },
  });
  chat._bindForm();
  return { chat, timers, ids,
    submit(text, stream = true) {
      ids.chatInput.value = text;
      ids.streamToggle.checked = stream;
      return ids.chatForm.events.submit({ preventDefault() {} });
    },
    selectMode(mode) { selectedMode = mode; },
    get startedModes() { return startedModes; },
    get responding() { return responding; },
    get idle() { return idle; },
  };
}

for (const firstDelivery of ['SSE reply', 'WebSocket receipt', 'WebSocket HTTP rejection']) {
  test(`${firstDelivery} settling cannot stop a second submitted response`, async () => {
    let posts = 0, completeSecond;
    const h = formHarness(async (url, options) => {
      if (options.method === 'POST') {
        posts++;
        if (posts === 2) return new Promise(resolve => { completeSecond = resolve; });
        if (firstDelivery === 'SSE reply') return streamReply('First answer');
        if (firstDelivery === 'WebSocket HTTP rejection') {
          return { ok: false, status: 503, json: async () => ({ accepted: false, detail: 'Queue full' }) };
        }
        return { ok: true, status: 200, json: async () => ({ accepted: true, task_id: 'first' }) };
      }
      assert.equal(url, '/api/chat/result/first');
      return { ok: true, status: 200,
        json: async () => ({ completed: true, task_id: 'first', reply: 'First answer' }) };
    });

    await h.submit('First question', firstDelivery === 'SSE reply');
    assert.equal(h.responding, 1);
    const firstTimerId = h.chat._settleTimer;
    const firstTimer = h.timers.get(firstTimerId);
    assert.equal(firstTimer.delay, 1000);
    assert.equal(firstTimer.cancelled, false);

    const second = h.submit('Second question');
    assert.equal(h.responding, 2);
    assert.equal(firstTimer.cancelled, true, 'new submission cancels the previous settle timer');
    // A timer callback may already be queued when clearTimeout runs. Identity
    // checks must protect the new response even if that old callback executes.
    firstTimer.callback();
    assert.equal(h.idle, 0);
    assert.equal(h.chat._settleTimer, null);

    completeSecond(streamReply('Second answer'));
    await second;
    const secondTimerId = h.chat._settleTimer;
    assert.notEqual(secondTimerId, firstTimerId);
    firstTimer.callback();
    assert.equal(h.chat._settleTimer, secondTimerId, 'an old callback cannot discard the current timer');
    assert.equal(h.idle, 0);
    h.timers.get(secondTimerId).callback();
    assert.equal(h.idle, 1);
    assert.equal(h.chat._settleTimer, null);
    assert.equal(h.chat.rendered, 'Second answer');
    assert.equal(posts, 2, 'each user submission is still sent exactly once');
  });
}

test('each submission snapshots its selected response mode into transport and message state', async () => {
  const requests = [];
  const h = formHarness(async (url, options) => {
    requests.push({url, options});
    return streamReply(options.body && JSON.parse(options.body).mode === 'deep' ? 'Deep answer' : 'Normal answer');
  });
  h.selectMode('deep');
  await h.submit('先深度回答');
  h.selectMode('normal');
  await h.submit('再正常回答');
  assert.deepEqual(requests.map(item => JSON.parse(item.options.body).mode), ['deep', 'normal']);
  assert.deepEqual(h.startedModes, ['deep', 'normal']);
  assert.equal(JSON.stringify(h.chat._messages.map(item => item.mode)), JSON.stringify(['deep', 'deep', 'normal', 'normal']));
  assert.equal(h.chat._messages[1].mode, 'deep');
  assert.equal(h.chat._messages[3].mode, 'normal');
  assert.equal(h.chat.rendered, 'Normal answer');
});

function wireResponse(wire, taskId = 'stream-task') {
  let sent = false;
  return {ok: true, headers: {get: name => name.toLowerCase() === 'x-eva-task-id' ? taskId : ''},
    body: {getReader: () => ({read: async () => {
      if (sent) return {done: true};
      sent = true;
      return {done: false, value: Buffer.from(wire)};
    }})}};
}

test('answer copy preserves raw Markdown and code without control labels', async () => {
  const copied = [], feedback = {textContent: ''};
  const chat = chatHarness(async () => {}, {navigator: {clipboard: {writeText: async text => copied.push(text)}},
    document: {getElementById: id => id === 'chatActionFeedback' ? feedback : null}});
  const text = '# 标题\n\n```html\n<img src=x onerror="alert(1)">\n```';
  const message = {text, role: 'eva'};
  const html = chat._renderMarkdown(text, 7);
  assert.match(html, /&lt;img src=x onerror=&quot;/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.equal(await chat._copyAnswer(message), true);
  assert.deepEqual(copied, [text]);
  assert.equal(feedback.textContent, '已复制');
  assert.doesNotMatch(chat._renderMarkdown('[unsafe](javascript:alert) [safe](https://example.com)'), /href="javascript:/);
});

for (const clipboard of [undefined, {writeText: async () => {throw new Error('denied');}}]) {
  test(`clipboard ${clipboard ? 'permission rejection' : 'unavailable'} offers manual-copy feedback`, async () => {
    const feedback = {textContent: ''};
    const chat = chatHarness(async () => {}, {navigator: {clipboard},
      document: {getElementById: id => id === 'chatActionFeedback' ? feedback : null}});
    assert.equal(await chat._copyAnswer({text: 'retain this answer'}), false);
    assert.match(feedback.textContent, /手动复制/);
  });
}

test('explicit retry creates one new request with the original mode and no repeated memory consent', async () => {
  const requests = [];
  let resolveRetry;
  const h = formHarness(async (url, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) return {ok: false, json: async () => ({accepted: false, detail: 'Queue full'})};
    if (requests.length > 2) return streamReply('正常回复');
    return new Promise(resolve => {resolveRetry = resolve;});
  });
  h.ids.rememberToggle.checked = true;
  h.selectMode('deep');
  await h.submit('原消息');
  const failed = h.chat._messages[1];
  assert.equal(failed.status, 'rejected');
  h.selectMode('normal');
  h.ids.chatInput.value = '保留草稿';
  const retry = h.chat._retryMessage(failed);
  assert.equal(await h.chat._retryMessage(failed), false);
  assert.equal(requests.length, 2);
  assert.equal(h.ids.chatInput.value, '保留草稿');
  assert.deepEqual(requests, [{text: '原消息', remember: true, mode: 'deep'}, {text: '原消息', remember: false, mode: 'deep'}]);
  assert.notEqual(h.chat._messages[3].id, failed.id);
  resolveRetry(streamReply('新回复'));
  await retry;
  assert.equal(h.chat._messages[3].mode, 'deep');
  assert.equal(h.chat._messages[3].status, 'completed');
  assert.equal(h.ids.sendBtn.disabled, false);
  await h.submit('下一消息');
  assert.equal(requests[2].mode, 'normal', 'retry does not alter the next selected mode');
});

test('POST transport failure remains unknown and cannot be retried as a definite failure', async () => {
  let posts = 0;
  const chat = chatHarness(async () => {posts++; throw new Error('network disconnected after write');});
  await chat._sendStream('may be admitted');
  const message = chat._messages[0];
  assert.equal(message.status, 'unknown');
  assert.equal(message.taskId, undefined);
  assert.match(chat._noticeText(message), /无法查询/);
  assert.equal(await chat._retryMessage(message), false);
  assert.equal(posts, 1);
});

test('an accepted acknowledgement without an ID remains unknown, never resubmitted', async () => {
  let posts = 0;
  const chat = chatHarness(async () => {posts++; return {ok: true, json: async () => ({accepted: true})};});
  await chat._sendViaWS('one write');
  assert.equal(chat._messages[0].status, 'unknown');
  assert.equal(posts, 1);
});

for (const ending of ['', 'data: [DONE]\n\n', 'data: [ERROR]\n\n']) {
  test(`SSE interruption ${ending || 'EOF'} retains partial text and receipt ID`, async () => {
    const chat = chatHarness(async () => wireResponse(`data: 已有部分\n\n${ending}`, 'retained-id'));
    await chat._sendStream('one write', false, 0, 'deep');
    assert.equal(chat._messages[0].text, '已有部分');
    assert.equal(chat._messages[0].taskId, 'retained-id');
    assert.equal(chat._messages[0].status, 'unknown');
    assert.equal(chat._messages[0].mode, 'deep');
    assert.equal(await chat._retryMessage(chat._messages[0]), false);
  });
}

test('named receipt that reports missing outcome retains partial text without claiming completion', async () => {
  const chat = chatHarness(async () => wireResponse('data: partial\n\nevent: result\ndata: {"completed":false,"error":"result_unknown_or_expired"}\n\n'));
  await chat._sendStream('one write');
  assert.equal(chat._messages[0].status, 'unknown');
  assert.equal(chat._messages[0].text, 'partial');
});

test('SSE supports CRLF and multiline event data, and ignores a conflicting task ID', async () => {
  const chat = chatHarness(async () => wireResponse('event: result\r\ndata: {"completed": true,\r\ndata: "task_id":"other", "reply":"wrong"}\r\n\r\ndata: correct partial\r\n\r\n'));
  await chat._sendStream('one write');
  assert.equal(chat._messages[0].text, 'correct partial');
  assert.equal(chat._messages[0].status, 'unknown');
});

test('manual query only reads the retained task, deduplicates clicks, and supports pending then complete', async () => {
  const calls = [];
  let release;
  const chat = chatHarness(async (url, options) => {
    calls.push({url, method: options.method || 'GET'});
    if (calls.length === 1) return wireResponse('data: partial\n\n', 'task/a');
    if (calls.length === 2) return new Promise(resolve => {release = resolve;});
    return {ok: true, json: async () => ({completed: true, task_id: 'task/a', ok: true, reply: 'finished'})};
  });
  await chat._sendStream('one write');
  const message = chat._messages[0];
  const first = chat._checkResult(message);
  await chat._checkResult(message);
  assert.equal(calls.length, 2);
  release({ok: true, status: 202, json: async () => ({completed: false, task_id: 'task/a', state: 'running'})});
  await first;
  assert.equal(message.status, 'waiting');
  await chat._checkResult(message);
  assert.equal(message.status, 'completed');
  assert.equal(message.text, 'finished');
  assert.deepEqual(calls.map(c => c.method), ['POST', 'GET', 'GET']);
  assert.equal(calls[1].url, '/api/chat/result/task%2Fa');
});

test('missing query receipt remains unknown and permits future reads without retrying the write', async () => {
  const calls = [];
  const chat = chatHarness(async (url, options) => {
    calls.push(options.method || 'GET');
    return url.endsWith('/stream') ? wireResponse('data: partial\n\n') : {ok: false, status: 404, json: async () => ({completed: false})};
  });
  await chat._sendStream('original');
  const message = chat._messages[0];
  await chat._checkResult(message);
  assert.equal(message.status, 'unknown');
  assert.equal(message.text, 'partial');
  assert.equal(message.taskId, 'stream-task');
  assert.equal(message.checking, false);
  assert.match(chat._noticeText(message), /无法确定结果/);
  assert.deepEqual(calls, ['POST', 'GET']);
});

test('a late WS answer updates its original message without settling a newer response', async () => {
  const h = formHarness(async () => wireResponse('data: first partial\n\n', 'first-task'));
  await h.submit('first');
  const first = h.chat._messages[1];
  const generation = h.chat._beginResponse();
  const second = h.chat._createReply('second', 'deep', true, generation);
  h.chat._onWSMessage({channel: 'chat_reply', payload: {task_id: 'first-task', completed: true, ok: true, reply: 'late answer', mode: 'deep'}});
  assert.equal(first.text, 'late answer');
  assert.equal(first.status, 'completed');
  assert.equal(first.mode, 'normal');
  assert.equal(second.status, 'submitting');
  assert.equal(h.chat._settleTimer, null);
});

test('a terminal WS answer wins over an older in-flight query response', async () => {
  let release;
  const chat = chatHarness(async url => url.endsWith('/stream') ? wireResponse('data: partial\n\n') : new Promise(resolve => {release = resolve;}));
  await chat._sendStream('original');
  const message = chat._messages[0];
  const query = chat._checkResult(message);
  chat._onWSMessage({channel: 'chat_reply', payload: {task_id: 'stream-task', completed: true, reply: 'authoritative'}});
  release({ok: true, status: 202, json: async () => ({completed: false, task_id: 'stream-task'})});
  await query;
  assert.equal(message.status, 'completed');
  assert.equal(message.text, 'authoritative');
});

test('execution uncertainty takes priority over an apparent failure; known failures alone enable retry', async () => {
  const chat = chatHarness(async () => {});
  const message = chat._createReply('one', 'deep', true, 0);
  chat._applyReceipt(message, {completed: true, terminal_state: 'failed', execution_state: 'may_still_be_running', ok: false, mode: 'normal'});
  assert.equal(message.status, 'unknown');
  assert.equal(message.mode, 'deep');
  assert.equal(await chat._retryMessage(message), false);
  chat._applyReceipt(message, {completed: true, terminal_state: 'failed', execution_state: 'not_started', ok: false});
  assert.equal(message.status, 'failed');
});

test('message rendering preserves keyboard focus and reading position during streaming', () => {
  let selector, focusOptions;
  const active = {dataset: {messageAction: 'copy', messageId: '1'}};
  const container = {scrollHeight: 900, clientHeight: 200, scrollTop: 100, contains: node => node === active,
    querySelector: value => {selector = value; return {focus: options => {focusOptions = options;}};}};
  const chat = chatHarness(async () => {}, {I18N: {lang: () => 'en', t: key => key},
    document: {activeElement: active, getElementById: id => id === 'chatMessages' ? container : null}});
  chat._messages = [{id: 1, role: 'eva', text: 'long answer', mode: 'deep', time: Date.now(), status: 'receiving'}];
  chat._realRender();
  assert.equal(container.scrollTop, 100);
  assert.equal(selector, 'button[data-message-id="1"][data-message-action="copy"]');
  assert.equal(focusOptions.preventScroll, true);
  assert.match(container.innerHTML, /Copy received text/);
  assert.match(container.innerHTML, /Receiving/);
  assert.doesNotMatch(container.innerHTML, /data-message-action="retry"/);
});

test('polling deadline retains association for a later WS reply and never resubmits', async () => {
  let calls = 0;
  const chat = chatHarness(async () => {calls++; throw new Error('offline');});
  const message = chat._createReply('original', 'normal', false, 0);
  message.taskId = 'timed-out-delivery';
  chat._pendingTasks[message.taskId] = {message, responseGeneration: 0};
  await chat._pollTask(message.taskId, Date.now());
  assert.equal(message.status, 'unknown');
  assert.equal(message.taskId, 'timed-out-delivery');
  assert.equal(Object.keys(chat._pendingTasks).length, 0);
  chat._onWSMessage({channel: 'chat_reply', payload: {task_id: message.taskId, completed: true, ok: true, reply: 'late receipt'}});
  assert.equal(message.text, 'late receipt');
  assert.equal(message.status, 'completed');
  assert.equal(calls, 1);
});

test('a late stream read error cannot downgrade a terminal WS delivery', async () => {
  let failRead;
  const chat = chatHarness(async () => ({ok: true, headers: {get: () => 'stream-task'},
    body: {getReader: () => ({read: () => new Promise((_, reject) => {failRead = reject;})})}}));
  const send = chat._sendStream('original');
  await new Promise(resolve => setImmediate(resolve));
  chat._onWSMessage({channel: 'chat_reply', payload: {task_id: 'stream-task', completed: true, ok: true, reply: 'terminal answer'}});
  failRead(new Error('stream connection lost'));
  await send;
  assert.equal(chat._messages[0].status, 'completed');
  assert.equal(chat._messages[0].text, 'terminal answer');
});

test('terminal announcements include uncertainty even when partial text was received', () => {
  const announcement = {textContent: ''}, status = {textContent: ''};
  const chat = chatHarness(async () => {}, {document: {getElementById: id => ({chatAnnouncement: announcement, chatDeliveryStatus: status})[id]}});
  const message = chat._createReply('original', 'normal', true, 0);
  message.text = '部分回复';
  message.taskId = 'admitted';
  chat._setOutcome(message, 'unknown', 'unknown');
  assert.match(announcement.textContent, /部分回复/);
  assert.match(announcement.textContent, /可能仍在执行/);
  assert.equal(status.textContent, '回复状态：结果未知');
});

test('malformed named receipt cannot turn a partial stream into a completed action', async () => {
  const chat = chatHarness(async () => wireResponse('data: partial\n\nevent: result\ndata: {"completed":"false","reply":"unverified"}\n\n'));
  await chat._sendStream('original');
  assert.equal(chat._messages[0].status, 'unknown');
  assert.equal(chat._messages[0].text, 'partial');
});
