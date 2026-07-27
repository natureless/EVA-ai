// EVA Chat — message history, SSE streaming, avatar state control
const Chat = {
  _messages: [],
  _streamAbort: null,

  init() {
    this._messages = [];
    this._bindForm();
    this._bindTextarea();
    this._bindMobileAvatar();
    window.addEventListener("lang-changed", () => this._rerenderMessages());
  },

  // ── Form handling ─────────────────────────────────────
  _bindForm() {
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatInput");
    const sendBtn = document.getElementById("sendBtn");

    if (!form || !input || !sendBtn) return;

    // Enter to send (shift+enter for newline)
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

      this._addMessage("user", text);
      AvatarController.setResponding();

      const useStream = document.getElementById("streamToggle")?.checked !== false;

      if (useStream) {
        await this._sendStream(text);
      } else {
        await this._sendSync(text);
      }

      sendBtn.disabled = false;
      sendBtn.textContent = I18N ? I18N.t("chat.send") : "Send";
    });
  },

  // ── Textarea focus/blur → avatar listening state ──────
  _bindTextarea() {
    const input = document.getElementById("chatInput");
    if (!input) return;

    // Auto-resize
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });

    input.addEventListener("focus", () => {
      AvatarController.setListening();
    });

    input.addEventListener("blur", () => {
      if (AvatarController._state === "listening") {
        AvatarController._setIdle();
      }
    });
  },

  // ── Mobile avatar tap-to-expand ───────────────────────
  _bindMobileAvatar() {
    const panel = document.getElementById("avatarPanel");
    if (!panel) return;
    panel.addEventListener("click", () => {
      if (window.innerWidth > 767) return;
      panel.classList.toggle("expanded");
    });
  },

  // ── Message rendering ─────────────────────────────────
  _addMessage(role, text) {
    this._messages.push({ role, text, time: Date.now() });
    this._renderMessages();
  },

  _updateLastEvaMessage(text) {
    // Update or append EVA message (used during streaming)
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

    // Build message list HTML
    container.innerHTML = this._messages.map((m, i) => {
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

    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
  },

  _rerenderMessages() {
    // Called when language changes to update sender labels and times
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

  // ── SSE Streaming ─────────────────────────────────────
  async _sendStream(text) {
    if (this._streamAbort) this._streamAbort.abort();
    const ctrl = new AbortController();
    this._streamAbort = ctrl;

    // Show typing indicator as an EVA placeholder
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
          if (data === "[DONE]") {
            break;
          }
          if (data.startsWith("[ERROR:")) {
            full = data;
            break;
          }
          full += data;
          this._updateLastEvaMessage(full);
          // Trigger mouth animation on each token
          AvatarController.mouthCycle();
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
      // Return to idle after a short pause
      setTimeout(() => {
        if (AvatarController._state === "responding") {
          AvatarController._setIdle();
        }
      }, 1000);
    }
  },

  // ── Synchronous (REST) chat ───────────────────────────
  async _sendSync(text) {
    this._messages.push({ role: "eva", text: "...", time: Date.now() });
    this._renderMessages();

    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const result = await r.json();
      this._updateLastEvaMessage(result.reply || "(no reply)");

      // Simulate mouth movement for sync responses
      let cycles = 0;
      const simMouth = setInterval(() => {
        AvatarController.mouthCycle();
        cycles++;
        if (cycles > 6) clearInterval(simMouth);
      }, 280);
    } catch (err) {
      this._updateLastEvaMessage(`Error: ${err.message}`);
    } finally {
      setTimeout(() => {
        if (AvatarController._state === "responding") {
          AvatarController._setIdle();
        }
      }, 1000);
    }
  },
};

// ── Init ────────────────────────────────────────────────
Nav.init();
AvatarController.init("avatarWrapper", "particleCanvas");
Chat.init();
