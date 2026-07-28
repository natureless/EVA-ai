// EVA Chat — WebSocket-backed message delivery, SSE streaming, particle control
const Chat = {
  _messages: [],
  _streamAbort: null,
  _ws: null,
  _wsReconnectTimer: null,
  _pendingTasks: {},

  init() {
    this._messages = [];
    this._connectWS();
    this._bindForm();
    this._bindTextarea();
    window.addEventListener("lang-changed", () => this._rerenderMessages());
  },

  // ── WebSocket connection ────────────────────────────────
  _connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;

    const ws = new WebSocket(url);
    this._ws = ws;

    ws.onopen = () => {
      this._wsReconnectTimer = null;
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        this._onWSMessage(msg);
      } catch (_) { /* ignore malformed */ }
    };

    ws.onclose = () => {
      // Reconnect after 3s if not intentionally closed
      if (!this._wsReconnectTimer) {
        this._wsReconnectTimer = setTimeout(() => this._connectWS(), 3000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  },

  _onWSMessage(msg) {
    const { channel, payload } = msg;
    if (!payload) return;

    // chat_token: per-token streaming from cognition loop
    if (channel === "chat_token") {
      const taskId = payload.task_id;
      if (!taskId) return;
      // Accumulate token for the pending task
      const pending = this._pendingTasks[taskId];
      if (pending && pending.onToken) {
        pending.fullReply += payload.token;
        pending.onToken(pending.fullReply);
        ParticleController.burstParticles(2);
      }
    }

    // chat_reply: final result from cognition loop
    if (channel === "chat_reply") {
      const taskId = payload.task_id;
      if (!taskId) return;
      const pending = this._pendingTasks[taskId];
      if (pending) {
        delete this._pendingTasks[taskId];
        if (pending.mode === "sync") {
          // Sync mode: receive full reply at once
          const reply = payload.reply || "(no reply)";
          this._updateLastEvaMessage(reply);
          ParticleController.burstParticles(8);
        }
        // Settle avatar after reply
        clearTimeout(pending._settleTimer);
        pending._settleTimer = setTimeout(() => {
          if (ParticleController._state === "responding") {
            ParticleController._setIdle();
          }
        }, 1000);
      }
    }
  },

  // ── Form handling ─────────────────────────────────────
  _bindForm() {
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatInput");
    const sendBtn = document.getElementById("sendBtn");

    if (!form || !input || !sendBtn) return;

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      input.style.height = "auto";
      sendBtn.disabled = true;
      sendBtn.textContent = "...";

      this._sending = true;
      this._addMessage("user", text);
      ParticleController.setResponding();

      const useStream = document.getElementById("streamToggle")?.checked !== false;

      if (useStream) {
        await this._sendStream(text);
      } else {
        await this._sendViaWS(text);
      }

      this._sending = false;
      sendBtn.disabled = false;
      sendBtn.textContent = I18N ? I18N.t("chat.send") : "Send";
    });
  },

  // ── Textarea focus/blur → avatar listening state ──────
  _bindTextarea() {
    const input = document.getElementById("chatInput");
    if (!input) return;

    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });

    input.addEventListener("focus", () => {
      ParticleController.setListening();
    });

    input.addEventListener("blur", () => {
      if (ParticleController._state === "listening" && !this._sending) {
        ParticleController._setIdle();
      }
    });
  },

  // ── Message rendering ─────────────────────────────────
  _addMessage(role, text) {
    this._messages.push({ role, text, time: Date.now() });
    this._renderMessages();
  },

  _updateLastEvaMessage(text) {
    const last = this._messages[this._messages.length - 1];
    if (last && last.role === "eva") {
      last.text = text;
    } else {
      this._messages.push({ role: "eva", text, time: Date.now() });
    }
    this._renderMessages();
  },

  _renderMessages() {
    const container = document.getElementById("chatMessages");
    const empty = document.getElementById("chatEmpty");
    if (!container) return;

    if (this._messages.length === 0) {
      container.innerHTML = "";
      if (empty) empty.style.display = "flex";
      return;
    }

    if (empty) empty.style.display = "none";

    container.innerHTML = this._messages.map((m) => {
      if (m.role === "eva" && m.text === "...") {
        return `<div class="message-row eva">
          <span class="message-sender">${I18N ? I18N.t("chat.eva") : "EVA"}</span>
          <div class="typing-indicator"><span></span><span></span><span></span></div>
        </div>`;
      }
      const isUser = m.role === "user";
      const senderLabel = isUser
        ? (I18N ? I18N.t("chat.you") : "You")
        : (I18N ? I18N.t("chat.eva") : "EVA");
      const timeStr = this._formatTime(m.time);
      const text = this._escapeHtml(m.text);
      return `<div class="message-row ${isUser ? "user" : "eva"}">
        <span class="message-sender">${senderLabel}</span>
        <div class="message-bubble">${text}</div>
        <span class="message-time">${timeStr}</span>
      </div>`;
    }).join("");

    container.scrollTop = container.scrollHeight;
  },

  _rerenderMessages() {
    this._renderMessages();
  },

  _escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  },

  _formatTime(ts) {
    const d = new Date(ts);
    const now = new Date();
    const diffMin = Math.floor((now - d) / 60000);
    if (diffMin < 1) return I18N ? I18N.t("chat.now") : "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  },

  // ── WebSocket sync mode ───────────────────────────────
  async _sendViaWS(text) {
    // Show typing indicator
    this._messages.push({ role: "eva", text: "...", time: Date.now() });
    this._renderMessages();

    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const ack = await r.json();
      const taskId = ack.task_id;
      if (!taskId) throw new Error("No task_id returned");

      // Register pending task — reply arrives via WS chat_reply
      this._pendingTasks[taskId] = {
        mode: "sync",
        fullReply: "",
        _settleTimer: null,
      };
    } catch (err) {
      this._updateLastEvaMessage(`Error: ${err.message}`);
      setTimeout(() => {
        if (ParticleController._state === "responding") {
          ParticleController._setIdle();
        }
      }, 1000);
    }
  },

  // ── SSE Streaming (unified pipeline via /api/chat/stream) ──
  async _sendStream(text) {
    if (this._streamAbort) this._streamAbort.abort();
    const ctrl = new AbortController();
    this._streamAbort = ctrl;

    this._messages.push({ role: "eva", text: "...", time: Date.now() });
    this._renderMessages();

    try {
      const r = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: ctrl.signal,
      });

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let full = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") break;
          if (data.startsWith("[ERROR:")) {
            full = data;
            break;
          }
          full += data;
          this._updateLastEvaMessage(full);
          ParticleController.burstParticles(2);
        }
      }

      if (!full) full = "(empty response)";
      this._updateLastEvaMessage(full);
    } catch (err) {
      if (err.name !== "AbortError") {
        this._updateLastEvaMessage(`Error: ${err.message}`);
      }
    } finally {
      this._streamAbort = null;
      setTimeout(() => {
        if (ParticleController._state === "responding") {
          ParticleController._setIdle();
        }
      }, 1000);
    }
  },
};

// ── Init ────────────────────────────────────────────────
Nav.init();
ParticleController.init("avatarWrapper", "particleCanvas");
Chat.init();
