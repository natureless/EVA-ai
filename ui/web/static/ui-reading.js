// Reading controls own scrolling only; requests and cube state stay with their controllers.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.EvaChatReading = api; api.init(root); }
})(globalThis, function () {
  'use strict';
  const mounted = new WeakMap();
  function init(root) {
    const container = root.document?.getElementById('chatMessages');
    const button = root.document?.getElementById('chatLatest');
    if (!container || !button) return null;
    if (mounted.has(container)) return mounted.get(container);
    let following = true;
    const atBottom = () => container.scrollHeight - container.clientHeight - container.scrollTop <= 80;
    function render() {
      const hide = atBottom();
      if (hide && root.document.activeElement === button) container.querySelector('.message-row:last-child')?.focus({preventScroll:true});
      button.hidden = hide;
      const translator = root.I18N || (typeof I18N !== 'undefined' ? I18N : null);
      button.textContent = translator?.lang() === 'en' ? '↓ Latest message' : '↓ 回到最新消息';
    }
    function onScroll() { following = atBottom(); render(); }
    function jump() {
      following = true;
      container.scrollTop = container.scrollHeight;
      render();
      container.querySelector('.message-row:last-child')?.focus({preventScroll:true});
    }
    const observer = root.ResizeObserver ? new root.ResizeObserver(() => {
      if (following) container.scrollTop = container.scrollHeight;
      render();
    }) : null;
    observer?.observe(container);
    container.addEventListener('scroll', onScroll, {passive:true});
    button.addEventListener('click', jump);
    root.addEventListener('lang-changed', render);
    const controller = {update({follow}) {following = follow; render();}, dispose() {
      observer?.disconnect();
      container.removeEventListener('scroll', onScroll);
      button.removeEventListener('click', jump);
      root.removeEventListener('lang-changed', render);
      mounted.delete(container);
    }};
    mounted.set(container, controller); render();
    return controller;
  }
  return Object.freeze({init});
});
