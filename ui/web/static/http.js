// Shared browser request boundary. Never replay a write after authentication fails.
(function (root) {
  'use strict';
  class LoginRequired extends Error {
    constructor() {
      super('登录状态已失效或无法验证，请重新登录后再试。');
      this.name = 'LoginRequired';
    }
  }
  let loginRequired = false;
  function requireLogin() {
    loginRequired = true;
    if (typeof document === 'undefined' || document.getElementById('evaLoginNotice')) return;
    const notice = document.createElement('div');
    notice.id = 'evaLoginNotice';
    notice.setAttribute('role', 'alert');
    notice.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:1000;width:max-content;max-width:calc(100% - 32px);box-sizing:border-box;padding:14px 18px;border:1px solid #8e769f;border-radius:12px;background:#252330;color:#f2edf7;box-shadow:0 8px 30px #0006;font:14px/1.6 system-ui;display:flex;align-items:center;gap:14px;flex-wrap:wrap';
    const text = document.createElement('span');
    text.textContent = '登录需要更新。请先复制未发送的文字，再重新登录。';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '重新登录';
    button.style.cssText = 'padding:6px 12px;border:1px solid #b39cc8;border-radius:7px;background:#655079;color:white;cursor:pointer;white-space:nowrap';
    button.addEventListener('click', () => root.location.reload());
    notice.append(text, button);
    document.body.append(notice);
  }
  function mediaType(response) {
    return (response.headers?.get('content-type') || '').split(';')[0].trim().toLowerCase();
  }
  async function request(url, options = {}) {
    if (loginRequired) throw new LoginRequired();
    const response = await root.fetch(url, { ...options, redirect: 'manual' });
    // Access login redirects are opaque to fetch. Handle before JSON/SSE/blob reads.
    const html = mediaType(response) === 'text/html';
    if (response.type === 'opaqueredirect' || response.status === 401 ||
        (response.status >= 300 && response.status < 400) ||
        (html && (response.ok || response.status === 403))) {
      requireLogin();
      throw new LoginRequired();
    }
    return response;
  }
  async function json(url, options = {}) {
    const response = await request(url, options);
    if (!response.ok) {
      const error = new Error(`请求失败（HTTP ${response.status}）`);
      error.status = response.status;
      throw error;
    }
    const type = mediaType(response);
    if (type && type !== 'application/json' && !type.endsWith('+json')) {
      throw new Error('服务返回了无法识别的数据，请稍后重试。');
    }
    return response.json();
  }
  async function archive(url, options = {}) {
    const response = await request(url, options);
    if (!response.ok) throw new Error(`导出失败（HTTP ${response.status}）`);
    if (mediaType(response) !== 'application/zip') {
      throw new Error('导出未返回 ZIP 文件，下载已取消。');
    }
    return response.blob();
  }
  root.EvaHttp = { request, json, archive, LoginRequired, requireLogin,
    get loginRequired() { return loginRequired; } };
})(globalThis);
