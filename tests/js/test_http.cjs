const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../ui/web/static/http.js'), 'utf8');
function harness(fetch, extra = {}) {
  const context = {fetch, ...extra};
  vm.runInNewContext(source, context);
  return context.EvaHttp;
}

test('JSON errors preserve HTTP status for write recovery without retrying', async () => {
  let calls=0;
  const api=harness(async () => {calls++;return Response.json({detail:'conflict'}, {status:409});});
  await assert.rejects(api.json('/api/goals', {method:'POST'}), {status:409});
  assert.equal(calls, 1);
});

for (const response of [
  {type: 'opaqueredirect', status: 0},
  new Response('Unauthorized', {status: 401}),
  new Response('<html>Login</html>', {headers: {'Content-Type': 'text/html'}}),
  new Response('<html>Login</html>', {status: 403, headers: {'Content-Type': 'text/html'}}),
]) {
  test(`Login response ${response.type}/${response.status} blocks ZIP download and repeated writes`, async () => {
    let calls = 0;
    const api = harness(async (_url, options) => {
      calls++;
      assert.equal(options.redirect, 'manual');
      return response;
    });
    await assert.rejects(api.archive('/api/obsidian/export'), {name: 'LoginRequired'});
    await assert.rejects(api.request('/api/chat', {method: 'POST'}), {name: 'LoginRequired'});
    assert.equal(calls, 1);
  });
}

test('Export validates ZIP content type before creating a download', async () => {
  const api = harness(async () => new Response('proxy error', {headers: {'Content-Type': 'text/plain'}}));
  await assert.rejects(api.archive('/api/obsidian/export'), /未返回 ZIP/);
  const valid = harness(async () => new Response(new Uint8Array([80, 75, 3, 4]), {
    headers: {'Content-Type': 'application/zip'},
  }));
  assert.equal((await valid.archive('/api/obsidian/export')).size, 4);
});

test('Valid JSON and SSE responses preserve request signals and body streams', async () => {
  const controller = new AbortController();
  const api = harness(async (url, options) => {
    assert.equal(options.signal, controller.signal);
    return url === '/json'
      ? Response.json({ok: true})
      : new Response('data: result\n\n', {headers: {'Content-Type': 'text/event-stream'}});
  });
  assert.deepEqual(await api.json('/json', {signal: controller.signal}), {ok: true});
  const stream = await api.request('/sse', {signal: controller.signal});
  assert.equal(await stream.text(), 'data: result\n\n');
});

test('Network failure and application permission rejection do not claim an expired login', async () => {
  const offline = harness(async () => {throw new TypeError('offline');});
  await assert.rejects(offline.request('/api/chat', {method: 'POST'}), /offline/);
  assert.equal(offline.loginRequired, false);
  const forbidden = harness(async () => Response.json({detail: 'Tool disabled'}, {status: 403}));
  const response = await forbidden.request('/api/tools/call', {method: 'POST'});
  assert.equal(response.status, 403);
  assert.equal(forbidden.loginRequired, false);
});

test('Reauthentication is an explicit button action; it does not submit or discard text automatically', () => {
  let banner, reloads = 0;
  const api = harness(async () => {}, {location: {reload: () => reloads++}, document: {
    getElementById: () => banner || null,
    createElement: () => ({style: {}, children: [], setAttribute() {},
      append(...nodes) {this.children.push(...nodes);},
      addEventListener(_event, fn) {this.click = fn;}}),
    body: {append(node) {banner = node;}},
  }});
  api.requireLogin();
  const first = banner;
  api.requireLogin();
  assert.equal(banner, first);
  assert.equal(reloads, 0);
  assert.match(banner.children[0].textContent, /先复制未发送/);
  banner.children[1].click();
  assert.equal(reloads, 1);
});
