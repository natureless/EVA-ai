// Register immutable file expectations against an existing server request.
(function (root) {
  'use strict';
  function payload(fields) {
    const task = fields.task.trim(), description = fields.description.trim();
    if (!task || task.length > 256) throw new Error('请填写已登记请求的 ID（最多 256 字符）。');
    if (!description || description.length > 1000) throw new Error('请填写目标描述（最多 1000 字符）。');
    const lines = fields.files.trim().split(/\r?\n/);
    if (!fields.files.trim() || lines.length > 16) throw new Error('请填写 1 至 16 个文件，每行一个。');
    const seen = new Set();
    const files = lines.map((line, index) => {
      const parts = line.split('|');
      const path = parts[0].trim(), sha256 = (parts[1] || '').trim().toLowerCase();
      if (parts.length !== 2 || !/^[a-f0-9]{64}$/.test(sha256)) {
        throw new Error(`第 ${index + 1} 行需要相对路径 | 64 位 SHA-256。`);
      }
      if (!path || path.length > 256 || /[\\:*?"<>|\x00]/.test(path) ||
          path.split('/').some(p => !p || p.startsWith('.') || /[. ]$/.test(p) ||
            /^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(p))) {
        throw new Error(`第 ${index + 1} 行路径无效，请使用工作区内的相对路径。`);
      }
      if (seen.has(path.toLowerCase())) throw new Error(`第 ${index + 1} 行重复指定同一文件。`);
      seen.add(path.toLowerCase());
      return {path, sha256};
    });
    let deadline = null;
    if (fields.deadline) {
      const date = new Date(fields.deadline);
      if (!Number.isFinite(date.getTime())) throw new Error('截止时间无效。');
      deadline = date.toISOString();
    }
    return {task_id: task, description, deadline,
      success_conditions: [{field: 'checks.files_match', operator: 'equals', expected: true}],
      verification: {kind: 'workspace_files_sha256_v1', files}};
  }
  root.EvaGoalCreate = {payload};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const dialog = $('goalCreateDialog'), form = $('goalCreateForm'), opener = $('newGoal');
  if (!dialog || !form || !opener || opener.disabled) return;
  const inputs = ['goalTask', 'goalDescription', 'goalFiles', 'goalDeadline'];
  let busy = false, mode = 'idle', boundTask = '', result = null;
  function render() {
    inputs.forEach(id => {$(id).disabled = busy || mode !== 'idle';});
    $('submitGoal').disabled = busy || mode !== 'idle';
    $('lookupGoal').disabled = busy || mode === 'success';
    $('viewCreatedGoal').hidden = mode !== 'success';
    $('anotherGoal').hidden = mode !== 'success';
    $('goalCreateForm').setAttribute('aria-busy', String(busy));
  }
  function notice(message) { $('goalCreateStatus').textContent = message; }
  function reveal() {
    root.dispatchEvent(new CustomEvent('eva:goal-created', {
      detail: {goal_id: result.goal_id, task_id: result.task_id},
    }));
  }
  function accepted(value, recovered) {
    if (!value || typeof value.goal_id !== 'string' || !value.goal_id || value.goal_id.length > 200 ||
        value.task_id !== boundTask) throw new Error('服务返回的登记结果无法确认。');
    result = value; mode = 'success';
    notice(`${recovered ? '该请求已登记目标' : '目标已登记'}：${value.goal_id}\n${value.description || ''}\n登记不等于完成；文件检查需要在目标详情中显式触发。`);
    reveal();
  }
  async function request(lookup, body) {
    if (busy) return;
    busy = true; render(); notice(lookup ? '正在查询已登记目标…' : '正在登记目标，请勿重复提交…');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const value = await root.EvaHttp.json(lookup
        ? '/api/goals/by-task/' + encodeURIComponent(boundTask) : '/api/goals', {
        signal: controller.signal, cache: 'no-store',
        ...(lookup ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
      });
      accepted(value, lookup);
    } catch (error) {
      if (lookup && error.status === 404) {
        mode = 'idle';
        notice('此次查询未找到已登记目标。请确认请求 ID；若前次提交超时，可稍后再次查询，或显式提交。服务会拒绝重复绑定。');
      } else if (!lookup && [403, 404, 422].includes(error.status)) {
        mode = 'idle';
        notice(error.status === 404 ? '请求不存在或回执已不在可绑定范围，请使用有效的请求 ID。'
          : error.status === 422 ? '登记条件无效，请检查字段、文件路径和哈希。' : '当前身份无权登记目标。');
      } else {
        mode = 'uncertain';
        notice(error.name === 'LoginRequired'
          ? '登录需要更新。请保留请求 ID，重新登录后先查询登记结果。'
          : '未能确认登记结果。请点击“查询登记结果”；不会自动重试提交。' + (error.status ? `（HTTP ${error.status}）` : ''));
      }
    } finally { clearTimeout(timer); busy = false; render(); }
  }
  opener.addEventListener('click', () => {render(); dialog.showModal();});
  $('closeGoalCreate').addEventListener('click', () => dialog.close());
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (busy || mode !== 'idle' || !form.reportValidity()) return;
    try {
      const body = payload({task: $('goalTask').value, description: $('goalDescription').value,
        files: $('goalFiles').value, deadline: $('goalDeadline').value});
      boundTask = body.task_id; request(false, body);
    } catch (error) {notice(error.message);}
  });
  $('lookupGoal').addEventListener('click', () => {
    if (busy || mode === 'success') return;
    const task = mode === 'uncertain' ? boundTask : $('goalTask').value.trim();
    if (!task || task.length > 256) {notice('请先填写有效的请求 ID。'); return;}
    boundTask = task; request(true);
  });
  $('viewCreatedGoal').addEventListener('click', () => {if (result) {reveal(); dialog.close();}});
  $('anotherGoal').addEventListener('click', () => {
    if (busy || mode !== 'success') return;
    mode = 'idle'; result = null; boundTask = ''; form.reset(); notice(''); render(); $('goalTask').focus();
  });
  render();
})(globalThis);
