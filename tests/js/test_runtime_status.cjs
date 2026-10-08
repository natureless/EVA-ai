const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const context = { AbortController, setTimeout, clearTimeout };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../ui/web/static/runtime_status.js"), "utf8"), context);
const status = context.EvaRuntimeStatus;
const live = { mode: "legacy", phase: "running", consumer_running: true, accepting_events: true,
  consumer_type: "core.cognition_loop.CognitionLoop", extensions: { mvsc: {status: "attached"} } };

test("MVSC attachment never labels the HTTP consumer as MVSC", () => {
  const view = status.describe(live);
  assert.match(view.text, /^Legacy · 运行中/);
  assert.match(view.text, /扩展已装配/);
  assert.match(view.detail, /未接入 HTTP/);
});
test("selected Minimal and stopped consumers use actual runtime observations", () => {
  assert.match(status.describe({...live, mode: "minimal", extensions: {}}).text, /^Minimal/);
  assert.match(status.describe({...live, consumer_running: false}).text, /未接收请求/);
  assert.match(status.describe({...live, phase: "failed"}).text, /未接收请求/);
});
test("failed attachment is distinct from an unavailable observation", () => {
  assert.match(status.describe({...live, extensions: {mvsc: {status: "failed"}}}).text, /装配失败/);
  assert.match(status.describe(null).text, /未知/);
});
test("failed refresh clears a previously running status, including HTTP errors", async () => {
  const element = {dataset: {}};
  await status.refresh(element, async () => ({ok: true, json: async () => live}));
  assert.equal(element.dataset.runtimeState, "running");
  await status.refresh(element, async () => ({ok: false}));
  assert.equal(element.dataset.runtimeState, "unknown");
  assert.match(element.textContent, /未知/);
  await status.refresh(element, async () => { throw new Error("offline"); });
  assert.equal(element.dataset.runtimeState, "unknown");
});
