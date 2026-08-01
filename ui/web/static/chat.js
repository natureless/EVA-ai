// EVA Chat — WebSocket-backed message delivery, SSE streaming, particle control
const Chat = {
  _messages: [],
  _streamAbort: null,
  _ws: null,
  _wsReconnectTimer: null,
  _wsReconnectAttempts: 0,
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

    this._setWsIndicator("connecting");
    const ws = new WebSocket(url);
    this._ws = ws;

    ws.onopen = () => {
      this._wsReconnectTimer = null;
      this._wsReconnectAttempts = 0;
      this._setWsIndicator("connected");
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        this._onWSMessage(msg);
      } catch (_) { /* ignore malformed */ }
    };

    ws.onclose = () => {
      this._setWsIndicator("disconnected");
      // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap
      if (!this._wsReconnectTimer) {
        this._wsReconnectAttempts++;
        const base = Math.min(1000 * Math.pow(2, this._wsReconnectAttempts - 1), 30000);
        const jitter = Math.random() * 1000;  // 0-1s random jitter
        const delay = base + jitter;
        this._wsReconnectTimer = setTimeout(() => this._connectWS(), delay);
      }
    };

    ws.onerror = () => {
      this._setWsIndicator("disconnected");
      ws.close();
    };
  },

  _setWsIndicator(state) {
    const el = document.getElementById("wsIndicator");
    if (!el) return;
    el.className = `badge ${state}`;
    el.title = { connected: "WebSocket connected", connecting: "WebSocket connecting…", disconnected: "WebSocket disconnected — reconnecting" }[state] || state;
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
      const html = isUser ? this._escapeHtml(m.text) : this._renderMarkdown(m.text);
      const toolCardsHtml = m.toolCards ? this._renderToolCards(m.toolCards) : "";
      return `<div class="message-row ${isUser ? "user" : "eva"}">
        <span class="message-sender">${senderLabel}</span>
        ${toolCardsHtml}
        <div class="message-bubble">${html}</div>
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

  // Full markdown → HTML for LLM replies (ChatGPT-style output)
  _renderMarkdown(s) {
    const raw = String(s ?? "");

    // ── Phase 0: protect fenced code blocks ──
    const fences = [];
    let html = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      fences.push({ lang, code: code.trimEnd() });
      return `\x00F${fences.length - 1}\x00`;
    });

    // Convert literal \n / \\n to real newlines (LLM sometimes outputs these)
    html = html.replace(/\\n/g, "\n");

    // ── Phase 1: escape HTML ──
    html = this._escapeHtml(html);

    // ── Phase 2: inline formatting ──
    html = html
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<em>$1</em>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/~~(.+?)~~/g, "<del>$1</del>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // ── Phase 3: block-level processing ──
    const lines = html.split("\n");
    const out = [];
    let i = 0;

    const _isFence = (l) => /^\x00F\d+\x00$/.test(l.trim());
    const _isHr = (l) => /^[-*_]{3,}$/.test(l.trim());
    const _isHeading = (l) => /^#{1,3}\s/.test(l.trim());
    const _isUl = (l) => /^\s*[-*+]\s/.test(l.trim());
    const _isOl = (l) => /^\s*\d+\.\s/.test(l.trim());
    const _isBlockquote = (l) => /^&gt;\s/.test(l.trim());
    const _isTable = (l) => l.trim().startsWith("|") && l.trim().endsWith("|");

    const _collectPara = () => {
      const buf = [];
      while (i < lines.length && lines[i].trim() !== ""
        && !_isFence(lines[i]) && !_isHr(lines[i]) && !_isHeading(lines[i])
        && !_isUl(lines[i]) && !_isOl(lines[i]) && !_isBlockquote(lines[i])
        && !_isTable(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      return buf;
    };

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // Blank line
      if (trimmed === "") { i++; continue; }

      // Restore fenced code block (with copy button)
      if (_isFence(line)) {
        const idx = parseInt(trimmed.match(/^\x00F(\d+)\x00$/)[1]);
        const f = fences[idx];
        const langLabel = f.lang ? `<span class="code-lang">${f.lang}</span>` : "";
        out.push(
          `<div class="chat-code-wrap">${langLabel}<button class="code-copy-btn" onclick="Chat._copyCode(this)">Copy</button>`
          + `<pre class="chat-code-block"><code>${f.code}</code></pre></div>`
        );
        i++; continue;
      }

      // Heading (# ## ###)
      if (_isHeading(trimmed)) {
        const m = trimmed.match(/^(#{1,3})\s+(.+)/);
        const level = parseInt(m[1].length) + 3; // h4-h6
        out.push(`<h${level} class="chat-h">${m[2]}</h${level}>`);
        i++; continue;
      }

      // Horizontal rule
      if (_isHr(trimmed)) {
        out.push('<hr class="chat-hr">');
        i++; continue;
      }

      // Blockquote
      if (_isBlockquote(trimmed)) {
        const bqLines = [];
        while (i < lines.length && _isBlockquote(lines[i])) {
          bqLines.push(lines[i].trim().replace(/^&gt;\s?/, ""));
          i++;
        }
        out.push(`<blockquote class="chat-quote">${bqLines.join("<br>")}</blockquote>`);
        continue;
      }

      // Unordered list
      if (_isUl(trimmed)) {
        out.push('<ul class="chat-list">');
        while (i < lines.length && _isUl(lines[i])) {
          const item = lines[i].trim().replace(/^\s*[-*+]\s*/, "");
          out.push(`<li>${item}</li>`);
          i++;
        }
        out.push('</ul>');
        continue;
      }

      // Ordered list
      if (_isOl(trimmed)) {
        out.push('<ol class="chat-list chat-list-ol">');
        while (i < lines.length && _isOl(lines[i])) {
          const item = lines[i].trim().replace(/^\s*\d+\.\s*/, "");
          out.push(`<li>${item}</li>`);
          i++;
        }
        out.push('</ol>');
        continue;
      }

      // Table
      if (_isTable(trimmed)) {
        const rows = [];
        while (i < lines.length && _isTable(lines[i])) {
          rows.push(lines[i].trim());
          i++;
        }
        out.push('<table class="chat-table"><tbody>');
        for (let r = 0; r < rows.length; r++) {
          if (r === 1 && /^[\|\s\-:]+$/.test(rows[r])) continue;
          const cells = rows[r].split("|").filter(c => c.trim());
          const tag = r === 0 ? "th" : "td";
          out.push("<tr>");
          cells.forEach(c => out.push(`<${tag}>${c.trim()}</${tag}>`));
          out.push("</tr>");
        }
        out.push('</tbody></table>');
        continue;
      }

      // Paragraph: collect consecutive non-special lines
      const paraLines = _collectPara();
      if (paraLines.length) {
        out.push(`<p>${paraLines.join("<br>")}</p>`);
      }
    }

    return out.join("");
  },

  _formatTime(ts) {
    const d = new Date(ts);
    const now = new Date();
    const diffMin = Math.floor((now - d) / 60000);
    if (diffMin < 1) return I18N ? I18N.t("chat.now") : "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  },

  _copyCode(btn) {
    const code = btn.closest(".chat-code-wrap")?.querySelector("code");
    if (!code) return;
    navigator.clipboard.writeText(code.textContent).then(() => {
      btn.textContent = "✓";
      setTimeout(() => { btn.textContent = "Copy"; }, 1500);
    }).catch(() => {
      btn.textContent = "Failed";
      setTimeout(() => { btn.textContent = "Copy"; }, 1500);
    });
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
      let toolCards = [];  // accumulated tool progress cards

      // Regex for tool progress events
      const TOOL_RE = /^\[(TOOL_START|TOOL_RESULT|TOOL_ERROR|TOOL_MAX_ROUNDS):([^\]]+)\]\s*(.*)$/;

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

          // Check for tool progress events (strip leading/trailing whitespace)
          const toolMatch = data.trim().match(TOOL_RE);
          if (toolMatch) {
            const [, eventType, toolName, detail] = toolMatch;
            toolCards.push({ eventType, toolName, detail: detail.trim() });
            // Render with tool cards inline
            this._updateLastEvaMessageWithTools(full, toolCards);
            continue;
          }

          full += data;
          this._updateLastEvaMessageWithTools(full, toolCards);
          ParticleController.burstParticles(2);
        }
      }

      if (!full && toolCards.length === 0) full = "(empty response)";
      this._updateLastEvaMessageWithTools(full, toolCards);
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

  // ── Render message with tool progress cards ──────────────
  _updateLastEvaMessageWithTools(fullText, toolCards) {
    const last = this._messages[this._messages.length - 1];
    if (last && last.role === "eva") {
      last.text = fullText;
      last.toolCards = toolCards.slice(); // copy
    } else {
      this._messages.push({ role: "eva", text: fullText, time: Date.now(), toolCards: toolCards.slice() });
    }
    this._renderMessages();
  },

  // ── Render tool cards as styled HTML ─────────────────────
  _renderToolCards(cards) {
    if (!cards || cards.length === 0) return "";
    const iconMap = {
      TOOL_START:   { cls: "tool-start",   icon: "⚙️" },
      TOOL_RESULT:  { cls: "tool-result",  icon: "✅" },
      TOOL_ERROR:   { cls: "tool-error",   icon: "❌" },
      TOOL_MAX_ROUNDS: { cls: "tool-warn", icon: "⚠️" },
    };
    const labelMap = {
      TOOL_START:   "Running",
      TOOL_RESULT:  "Done",
      TOOL_ERROR:   "Failed",
      TOOL_MAX_ROUNDS: "Limit",
    };
    return cards.map(c => {
      const cfg = iconMap[c.eventType] || { cls: "tool-start", icon: "🔧" };
      const label = labelMap[c.eventType] || c.eventType;
      const toolLabel = c.toolName.replace(/_/g, " ");
      return `<div class="tool-card ${cfg.cls}">
        <span class="tool-icon">${cfg.icon}</span>
        <span class="tool-label">${label}</span>
        <span class="tool-name">${this._escapeHtml(toolLabel)}</span>
        <span class="tool-detail">${this._escapeHtml(c.detail)}</span>
      </div>`;
    }).join("");
  },
};

// ── Init ────────────────────────────────────────────────
Nav.init();
ParticleController.init("avatarWrapper", "particleCanvas");
Chat.init();
