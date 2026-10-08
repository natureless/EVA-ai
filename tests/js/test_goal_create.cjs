const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../ui/web/static/goal_create.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function pure() {const ctx = {};vm.runInNewContext(source, ctx);return ctx.EvaGoalCreate.payload;}
const fields = (extra = {}) => ({task:'task-1', description:'确认报告', files:'docs/报告 final.md | ' + 'A'.repeat(64), deadline:'', ...extra});

test('form builds a fixed file-only contract and converts local deadline to UTC', () => {
  const input = fields({deadline:'2030-01-01T12:00'}), body = pure()(input);
  assert.deepEqual(JSON.parse(JSON.stringify(body)), {
    task_id:'task-1', description:'确认报告', deadline:new Date(input.deadline).toISOString(),
    success_conditions:[{field:'checks.files_match', operator:'equals', expected:true}],
    verification:{kind:'workspace_files_sha256_v1', files:[{path:'docs/报告 final.md', sha256:'a'.repeat(64)}]},
  });
});
test('file list permits 16 bounded entries and rejects an extra entry', () => {
  const lines = Array.from({length:16}, (_, i) => `file-${i}.md | ${'a'.repeat(64)}`);
  assert.equal(pure()(fields({files:lines.join('\r\n')})).verification.files.length, 16);
  assert.throws(() => pure()(fields({files:lines.concat('extra').join('\n')})), /1 至 16/);
});
for (const files of ['../secret', '/absolute', 'C:/secret', '.hidden', 'dir//file', 'dir\\file', 'NUL.txt', 'file.', 'dir/file /x', 'file']) {
  test('reject unsafe path or malformed entry: ' + files, () => {
    assert.throws(() => pure()(fields({files:files+' | '+'a'.repeat(63)})));
    if (files !== 'file') assert.throws(() => pure()(fields({files:files+' | '+'a'.repeat(64)})), /路径无效/);
  });
}
test('duplicate paths, empty required fields and invalid dates are rejected', () => {
  for (const extra of [{task:' '}, {description:' '}, {task:'t'.repeat(257)}, {description:'d'.repeat(1001)},
    {files:''}, {deadline:'not-a-date'}, {files:'a.md | '+'a'.repeat(64)+'\nA.md | '+'b'.repeat(64)}]) {
    assert.throws(() => pure()(fields(extra)));
  }
});

function harness(preview = false) {
  const dom = new Map(), requests = [], events = [], timers = new Map(); let serial = 0;
  function get(id) {
    if (!dom.has(id)) dom.set(id, {value:'', disabled:false, hidden:false, textContent:'', events:{}, open:false,
      addEventListener(name, fn) {this.events[name] = fn;}, setAttribute() {}, focus() {},
      showModal() {this.open = true;}, close() {this.open = false;}, reportValidity() {return true;},
      reset() {for (const field of ['goalTask','goalDescription','goalFiles','goalDeadline']) get(field).value = '';}});
    return dom.get(id);
  }
  get('newGoal').disabled = preview;
  const context = {document:{getElementById:get}, AbortController,
    CustomEvent:class {constructor(type, options) {this.type=type;this.detail=options.detail;}},
    dispatchEvent:event => events.push(event),
    setTimeout:(fn, ms) => {const id = ++serial;timers.set(id, {fn, ms});return id;},
    clearTimeout:id => timers.delete(id),
    EvaHttp:{json:(url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}))},
  };
  vm.runInNewContext(source, context);
  function fill() {
    const f=fields(); get('goalTask').value=f.task;get('goalDescription').value=f.description;
    get('goalFiles').value=f.files;get('goalDeadline').value=f.deadline;
  }
  return {get, requests, events, fill, timers,
    click:id => get(id).events.click?.(), submit:() => get('goalCreateForm').events.submit?.({preventDefault(){}}),
    timeout:() => [...timers.values()].forEach(t => t.fn()),
  };
}
const registered = {goal_id:'goal-1', task_id:'task-1', description:'已存描述', status:'pending_verification'};

test('one POST survives closing and reopening dialog, success refreshes graph without automatic verify', async () => {
  const h = harness();h.fill();h.click('newGoal');h.submit();h.submit();
  h.click('closeGoalCreate');h.click('newGoal');h.submit();h.click('lookupGoal');
  assert.equal(h.requests.length, 1);assert.equal(h.requests[0].options.method, 'POST');
  assert.equal(h.requests[0].options.signal.aborted, false);assert.equal(h.get('goalTask').disabled, true);
  h.requests[0].resolve(registered);await flush();
  assert.equal(h.events.length, 1);assert.equal(h.events[0].type, 'eva:goal-created');
  assert.deepEqual(JSON.parse(JSON.stringify(h.events[0].detail)), {goal_id:'goal-1', task_id:'task-1'});
  assert.match(h.get('goalCreateStatus').textContent, /登记不等于完成/);
  assert.equal(h.get('submitGoal').disabled, true);assert.equal(h.timers.size, 0);
  h.click('viewCreatedGoal');assert.equal(h.get('goalCreateDialog').open, false);
  h.click('anotherGoal');assert.equal(h.get('goalTask').value, '');assert.equal(h.get('submitGoal').disabled, false);
});
for (const error of [new TypeError('offline'), Object.assign(new Error('timeout'), {name:'AbortError'}),
  Object.assign(new Error('conflict'), {status:409}), Object.assign(new Error('unavailable'), {status:503}),
  Object.assign(new Error('login'), {name:'LoginRequired'})]) {
  test('uncertain create ' + (error.status || error.name) + ' requires lookup; never replays POST', async () => {
    const h=harness();h.fill();h.submit();h.requests[0].reject(error);await flush();
    h.submit();h.click('newGoal');h.submit();assert.equal(h.requests.length, 1);
    assert.equal(h.get('goalTask').disabled, true);
    h.click('lookupGoal');assert.equal(h.requests[1].url, '/api/goals/by-task/task-1');
    assert.equal(h.requests[1].options.method, undefined);
    h.requests[1].resolve(registered);await flush();
    assert.match(h.get('goalCreateStatus').textContent, /该请求已登记目标/);
    assert.match(h.get('goalCreateStatus').textContent, /已存描述/);
    assert.equal(h.requests.filter(r => r.options.method === 'POST').length, 1);
  });
}
test('timeout aborts request but lookup failure keeps uncertainty; confirmed absence permits explicit corrected submission', async () => {
  const h=harness();h.fill();h.submit();h.timeout();assert.equal(h.requests[0].options.signal.aborted, true);
  h.requests[0].reject(new TypeError('offline'));await flush();h.click('lookupGoal');
  h.requests[1].reject(new TypeError('offline'));await flush();h.submit();assert.equal(h.requests.length, 2);
  h.click('lookupGoal');h.requests[2].reject(Object.assign(new Error('absent'), {status:404}));await flush();
  assert.equal(h.get('goalTask').disabled, false);assert.equal(h.get('goalDescription').value, '确认报告');
  assert.match(h.get('goalCreateStatus').textContent, /显式提交/);assert.equal(h.requests.length, 3);
  h.get('goalTask').value='task-2';h.submit();assert.equal(JSON.parse(h.requests[3].options.body).task_id, 'task-2');
});
for (const status of [403,404,422]) {
  test('known pre-commit rejection ' + status + ' preserves editable draft', async () => {
    const h=harness();h.fill();h.submit();h.requests[0].reject(Object.assign(new Error('rejected'), {status}));await flush();
    assert.equal(h.get('goalTask').disabled, false);assert.equal(h.get('goalFiles').value, fields().files);
    assert.equal(h.requests.length, 1);assert.equal(h.events.length, 0);
  });
}
test('malformed or unrelated response cannot claim confirmed registration', async () => {
  const h=harness();h.fill();h.submit();h.requests[0].resolve({...registered, task_id:'other-task'});await flush();
  assert.equal(h.events.length, 0);assert.equal(h.get('submitGoal').disabled, true);
  assert.match(h.get('goalCreateStatus').textContent, /未能确认/);
});
test('invalid/native-invalid forms and read-only preview never issue writes', () => {
  const h=harness();h.fill();h.get('goalFiles').value='bad';h.submit();assert.equal(h.requests.length, 0);
  h.fill();h.get('goalCreateForm').reportValidity=() => false;h.submit();assert.equal(h.requests.length, 0);
  const preview=harness(true);preview.fill();preview.submit();preview.click('newGoal');assert.equal(preview.requests.length, 0);
});
