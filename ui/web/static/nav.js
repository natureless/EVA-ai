// EVA — shared navigation bar (WebSocket, theme, language, nav)
const Nav = {
  _ws: null,
  _wsReconnectTimer: null,

  init() {
    this._connectWS();
    this._bindTheme();
    this._bindLang();
  },

  // ── WebSocket ──────────────────────────────────────────
  _connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws?channel=system_state`;
    try {
      this._ws = new WebSocket(url);
    } catch (_) {
      this._setWS(false);
      this._scheduleReconnect();
      return;
    }
    this._ws.onopen = () => {
      this._setWS(true);
      if (this._wsReconnectTimer) { clearTimeout(this._wsReconnectTimer); this._wsReconnectTimer = null; }
    };
    this._ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "pong") return;
        // Fire a custom event so page scripts can listen
        window.dispatchEvent(new CustomEvent("ws-message", { detail: data }));
      } catch (_) { /* ignore parse errors */ }
    };
    this._ws.onclose = () => { this._setWS(false); this._scheduleReconnect(); };
    this._ws.onerror = () => { this._ws && this._ws.close(); };
  },

  _scheduleReconnect() {
    if (this._wsReconnectTimer) return;
    this._wsReconnectTimer = setTimeout(() => {
      this._wsReconnectTimer = null;
      this._connectWS();
    }, 3000);
  },

  _setWS(live) {
    const el = document.getElementById("wsIndicator");
    if (!el) return;
    if (live) {
      el.textContent = I18N.t("ws.live");
      el.className = "badge connected";
    } else {
      el.textContent = "WS";
      el.className = "badge disconnected";
    }
  },

  // ── Theme ──────────────────────────────────────────────
  _bindTheme() {
    globalThis.EvaTheme?.bind();
  },

  // ── Language ───────────────────────────────────────────
  _bindLang() {
    const btn = document.getElementById("langBtn");
    if (!btn) return;
    this._updateLangBtn(btn);
    btn.onclick = () => {
      I18N.toggle();
      this._updateLangBtn(btn);
      I18N.applyDOM();
      // Notify page scripts to re-render
      window.dispatchEvent(new CustomEvent("lang-changed"));
    };
  },

  _updateLangBtn(btn) {
    btn.textContent = I18N.lang() === "zh" ? "EN" : "中";
  },
};
