const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../ui/web/static/memory_diagnostics.js'), 'utf8');

function harness() {
  function element() {
    return {textContent: '', children: [], disabled: false, open: false, events: {}, isConnected: true,
      focus() {this.focused = true;},
      classList: {add() {}}, setAttribute() {},
      append(...nodes) {this.children.push(...nodes);},
      replaceChildren(...nodes) {this.children = nodes;},
      addEventListener(name, fn) {this.events[name] = fn;},
      showModal() {this.open = true;}, close() {this.open = false; this.events.close();}};
  }
  const ids = ['relationDiagnostics', 'openDiagnostics', 'closeDiagnostics', 'diagnosticPrevious',
    'diagnosticNext', 'diagnosticRetry', 'diagnosticItems', 'diagnosticStatus', 'diagnosticPagination',
    'diagnosticRecord', 'diagnosticRecordTitle', 'diagnosticRecordId', 'diagnosticRecordBody',
    'diagnosticRecordStatus', 'diagnosticRecordRetry', 'diagnosticRecordBack'];
  const dom = Object.fromEntries(ids.map(id => [id, element()]));
  const requests = [];
  vm.runInNewContext(source, {URLSearchParams, AbortController, setTimeout, clearTimeout,
    document: {getElementById: id => dom[id], createElement: element, createDocumentFragment: element},
    EvaHttp: Object.fromEntries(['json', 'request'].map(method => [method,
      (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}))])),
  });
  return {dom, requests};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const item = {relation: '<img src=x onerror=alert(1)>',
  source: {record_id: 'source', label: '现有实体', missing: false},
  target: {record_id: '<script>absent</script>', label: null, missing: true},
  provenance: {epistemic_status: 'unknown'}, timestamp: '2026-09-25'};
const page = (offset = 0, next = null) => ({items: [item], total: 51, offset, next_offset: next});

test('diagnostics paginate and render stored text literally without HTML', async () => {
  const {dom, requests} = harness();
  dom.openDiagnostics.onclick();
  assert.equal(dom.diagnosticNext.disabled, true);
  requests[0].resolve(page(0, 50)); await flush();
  const card = dom.diagnosticItems.children[0].children[0];
  assert.equal(card.children[0].textContent, item.relation);
  assert.equal(card.children[2].children[1].textContent, item.target.record_id);
  assert.match(card.children[2].children[0].textContent, /实体缺失/);
  assert.equal(dom.diagnosticPrevious.disabled, true);
  assert.equal(dom.diagnosticNext.disabled, false);
  dom.diagnosticNext.onclick();
  assert.match(requests[1].url, /offset=50/);
  requests[1].resolve(page(50)); await flush();
  assert.match(dom.diagnosticStatus.textContent, /51–51/);
  assert.equal(dom.diagnosticNext.disabled, true);
  dom.diagnosticPrevious.onclick();
  assert.match(requests[2].url, /offset=0/);
  requests[2].resolve(page(0, 50)); await flush();
});

test('closing aborts requests and late results cannot overwrite a reopened dialog', async () => {
  const {dom, requests} = harness();
  dom.openDiagnostics.onclick();
  dom.closeDiagnostics.onclick();
  assert.equal(requests[0].options.signal.aborted, true);
  dom.openDiagnostics.onclick();
  requests[1].resolve({items: [], total: 0, offset: 0, next_offset: null}); await flush();
  requests[0].resolve(page()); await flush();
  assert.match(dom.diagnosticStatus.textContent, /未发现/);
  assert.equal(dom.diagnosticItems.children[0].children.length, 0);
});

test('failed reads show errors instead of a healthy empty graph and can be retried', async () => {
  const {dom, requests} = harness();
  dom.openDiagnostics.onclick();
  requests[0].reject(new Error('读取失败（HTTP 503）')); await flush();
  assert.match(dom.diagnosticStatus.textContent, /503/);
  assert.equal(dom.diagnosticRetry.disabled, false);
  assert.equal(dom.diagnosticNext.disabled, true);
  dom.diagnosticRetry.onclick();
  requests[1].resolve(page()); await flush();
  assert.match(dom.diagnosticStatus.textContent, /共 51/);
});

const node = {label: '<img src=x onerror=alert(1)>', kind: 'user_message',
  content: '<script>stored event text</script>', content_truncated: false,
  provenance: {epistemic_status: 'assistant_inference'}, source: 'chat'};
const response = (value = node, status = 200) => ({status, ok: status === 200, json: async () => value});
async function loaded(originId = 'older/event?name=记录 & version=1') {
  const result = harness();
  result.dom.openDiagnostics.onclick();
  result.requests[0].resolve({...page(50), items: [{...item,
    provenance: {...item.provenance, source_event_id: originId}}]});
  await flush();
  result.card = result.dom.diagnosticItems.children[0].children[0];
  return result;
}

test('existing endpoints and source events open exact records independently of graph projection', async () => {
  const {dom, requests, card} = await loaded();
  const endpointButton = card.children[1].children.at(-1);
  assert.equal(endpointButton.textContent, '查看实体');
  assert.equal(card.children[2].children.some(child => child.onclick), false);
  endpointButton.onclick();
  assert.equal(new URLSearchParams(requests[1].url.split('?')[1]).get('tier'), 'S4');
  assert.equal(new URLSearchParams(requests[1].url.split('?')[1]).get('record_id'), 'source');
  requests[1].resolve(response()); await flush();
  assert.equal(dom.diagnosticRecordBody.children[0].textContent, node.label);
  assert.equal(dom.diagnosticRecordBody.children[5].textContent, node.content);
  assert.equal(dom.diagnosticItems.hidden, true);
  assert.equal(dom.diagnosticRecordTitle.focused, true);
  dom.diagnosticRecordBack.onclick();
  assert.equal(dom.diagnosticItems.hidden, false);
  assert.equal(dom.diagnosticRecord.hidden, true);
  assert.equal(endpointButton.focused, true);
  assert.equal(dom.diagnosticItems.children[0].children[0], card);
  assert.match(dom.diagnosticStatus.textContent, /51–51/);
  card.children.at(-1).onclick();
  const params = new URLSearchParams(requests[2].url.split('?')[1]);
  assert.equal(params.get('tier'), 'S5');
  assert.equal(params.get('record_id'), 'older/event?name=记录 & version=1');
  requests[2].resolve(response({...node, content_truncated: true})); await flush();
  assert.match(dom.diagnosticRecordStatus.textContent, /截断/);
});

test('missing and failed records remain distinguishable and retryable', async () => {
  const {dom, requests, card} = await loaded();
  card.children.at(-1).onclick();
  requests[1].resolve(response(null, 404)); await flush();
  assert.match(dom.diagnosticRecordStatus.textContent, /未找到该来源事件/);
  assert.equal(dom.diagnosticRecordBody.children.length, 0);
  assert.equal(dom.diagnosticRecordRetry.disabled, false);
  dom.diagnosticRecordRetry.onclick();
  assert.equal(requests[2].url, requests[1].url);
  requests[2].resolve(response(null, 503)); await flush();
  assert.match(dom.diagnosticRecordStatus.textContent, /503/);
  assert.doesNotMatch(dom.diagnosticRecordStatus.textContent, /未找到/);
  dom.diagnosticRecordRetry.onclick();
  requests[3].reject(Object.assign(new Error('aborted'), {name: 'AbortError'})); await flush();
  assert.match(dom.diagnosticRecordStatus.textContent, /读取超时/);
  dom.diagnosticRecordRetry.onclick();
  requests[4].resolve(response()); await flush();
  assert.match(dom.diagnosticRecordStatus.textContent, /已读取原始记录/);
});

test('returning and closing abort detail reads and reject stale results even during JSON decoding', async () => {
  const {dom, requests, card} = await loaded();
  card.children.at(-1).onclick();
  let finishDecode;
  requests[1].resolve({ok: true, status: 200, json: () => new Promise(resolve => {finishDecode = resolve;})});
  await flush();
  dom.diagnosticRecordBack.onclick();
  assert.equal(requests[1].options.signal.aborted, true);
  card.children[1].children.at(-1).onclick();
  requests[2].resolve(response({...node, label: 'new detail'})); await flush();
  finishDecode(node); await flush();
  assert.equal(dom.diagnosticRecordBody.children[0].textContent, 'new detail');
  dom.diagnosticRecordRetry.onclick();
  dom.closeDiagnostics.onclick();
  assert.equal(requests[3].options.signal.aborted, true);
  dom.openDiagnostics.onclick();
  requests[4].resolve(page()); await flush();
  requests[3].resolve(response()); await flush();
  assert.equal(dom.diagnosticRecord.hidden, true);
  assert.equal(dom.diagnosticRecordBody.children.length, 0);
  assert.equal(dom.diagnosticItems.children[0].children[0].children.some(child => child.textContent === '查看来源事件'), false);
});
