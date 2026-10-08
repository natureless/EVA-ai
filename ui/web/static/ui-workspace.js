// Product layout only: disclosure never replaces a cube or changes its journal.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else { root.EvaWorkspaceUI = api; api.init(root); }
})(globalThis, function () {
  "use strict";
  const mounted = new WeakMap();
  function initTabOrientation(root) {
    const tabs = root.document?.querySelector(".settings-nav [role=tablist]");
    if (!tabs) return null;
    if (mounted.has(tabs)) return mounted.get(tabs);
    const narrow = root.matchMedia("(max-width: 620px)");
    const render = () => tabs.setAttribute("aria-orientation", narrow.matches ? "horizontal" : "vertical");
    narrow.addEventListener("change", render);
    const controller = {render, dispose() { narrow.removeEventListener("change", render); mounted.delete(tabs); }};
    mounted.set(tabs, controller); render();
    return controller;
  }
  function init(root) {
    const document = root.document;
    const panel = document?.getElementById("cubePanel");
    const button = document?.getElementById("cubeDisclosure");
    const details = document?.getElementById("cubeDetails");
    const orders = document?.getElementById("cubeOrderControls");
    if (!panel || !button || !details || !orders) return initTabOrientation(root);
    if (mounted.has(panel)) return mounted.get(panel);
    const narrow = root.matchMedia("(max-width: 900px)");
    let expanded = false;
    function render() {
      const compact = narrow.matches;
      const hideDetails = !expanded;
      const active = document.activeElement;
      if (hideDetails && (details.contains(active) || orders.contains(active))) button.focus();
      button.hidden = false;
      button.setAttribute("aria-expanded", String(!hideDetails));
      panel.dataset.compact = String(compact);
      panel.dataset.expanded = String(!hideDetails);
      details.hidden = hideDetails;
      orders.hidden = hideDetails;
      const key = expanded ? "ui.cubeCollapse" : "ui.cubeExpand";
      const translator = root.I18N || (typeof I18N !== "undefined" ? I18N : null);
      const text = translator?.t(key) || (expanded ? "收起魔方演示" : "展开魔方演示");
      button.querySelector("span").textContent = text;
      button.setAttribute("aria-label", text);
    }
    function toggle() { expanded = !expanded; render(); }
    function escape(event) {
      if(event.key!=="Escape"||event.isComposing||event.defaultPrevented||!expanded)return;
      const active=document.activeElement;
      if(!details.contains(active)&&!orders.contains(active)&&active!==document.getElementById("cubeStage")&&active!==button)return;
      event.preventDefault();expanded=false;render();button.focus();
    }
    button.addEventListener("click", toggle);
    panel.addEventListener("keydown",escape);
    narrow.addEventListener("change", render);
    root.addEventListener("lang-changed", render);
    const controller = { render, dispose() {
      button.removeEventListener("click", toggle);
      panel.removeEventListener("keydown",escape);
      narrow.removeEventListener("change", render);
      root.removeEventListener("lang-changed", render);
      mounted.delete(panel);
    } };
    mounted.set(panel, controller);
    render();
    return controller;
  }
  return Object.freeze({init});
});
