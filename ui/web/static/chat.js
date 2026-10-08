// EVA Chat — WebSocket-backed message delivery, SSE streaming, avatar control
const Chat = {
  _messages: [],
  _streamAbort: null,
  _ws: null,
  _wsReconnectTimer: null,
  _wsReconnectAttempts: 0,
  _pendingTasks: {},
  _settleTimer: null,
  _responseGeneration: 0,
  _messageSequence: 0,
  _inputLengthError: null,
  _maxMessageCharacters: 4000,

  init() {
    this._messages = [];
    this._connectWS();
    this._bindForm();
    this._bindTextarea();
    this._bindMessageActions();
    this._loadModes();
    window.addEventListener("lang-changed", () => {
      this._rerenderMessages();
      this._actionFeedback("");
      this._renderInputError();
      if (this._wsState) this._setWsIndicator(this._wsState);
      this._renderDeliveryStatus();
    });
  },

  async _loadModes() {
    try {
      const response = await EvaHttp.request("/api/chat/modes");
      if (response.ok) ParticleController.setModeCapabilities?.((await response.json()).modes);
      else ParticleController.setModeCapabilityError?.();
    } catch (_) {
      // Mode selection remains usable; make the missing capability read explicit.
      ParticleController.setModeCapabilityError?.();
    }
  },

  // ── WebSocket connection ────────────────────────────────
  _connectWS() {
    if (globalThis.EvaHttp?.loginRequired) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;

    this._setWsIndicator("connecting");
    const ws = new WebSocket(url);
    this._ws = ws;

    ws.onopen = () => {
      if (this._ws !== ws) return;
      this._wsReconnectTimer = null;
      this._wsReconnectAttempts = 0;
      this._setWsIndicator("connected");
    };

    ws.onmessage = (e) => {
      if (this._ws !== ws) return;
      try {
        const msg = JSON.parse(e.data);
        this._onWSMessage(msg);
      } catch (_) { /* ignore malformed */ }
    };

    ws.onclose = (event) => {
      if (this._ws !== ws) return;
      this._setWsIndicator("disconnected");
      if (event?.code === 4401 || event?.code === 4403) globalThis.EvaHttp?.requireLogin();
      if (globalThis.EvaHttp?.loginRequired) return;
      // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap
      if (!this._wsReconnectTimer) {
        this._wsReconnectAttempts++;
        const base = Math.min(1000 * Math.pow(2, this._wsReconnectAttempts - 1), 30000);
        const jitter = Math.random() * 1000;  // 0-1s random jitter
        const delay = base + jitter;
        this._wsReconnectTimer = setTimeout(() => {
          this._wsReconnectTimer = null;
          this._connectWS();
        }, delay);
      }
    };

    ws.onerror = () => {
      if (this._ws !== ws) return;
      this._setWsIndicator("disconnected");
      ws.close();
    };
  },

  _setWsIndicator(state) {
    this._wsState = state;
    const el = document.getElementById("wsIndicator");
    if (!el) return;
    el.className = `badge ${state}`;
    el.textContent = (typeof I18N !== "undefined" && I18N.lang() === "en"
      ? { connected: "Connected", connecting: "Connecting", disconnected: "Offline" }
      : { connected: "已连接", connecting: "连接中", disconnected: "未连接" })[state] || state;
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
      const message = pending?.message || this._messages.find(m => m.role === "eva" && m.taskId === taskId);
      // Local waiting may have ended; retain the association for late delivery.
      if (message && !["completed", "failed", "rejected"].includes(message.status) && this._applyReceipt(message, payload)) {
        delete this._pendingTasks[taskId];
        ParticleController.burstParticles(8);
        this._scheduleSettle(message.responseGeneration);
      }
    }
  },

  _beginResponse() {
    this._actionFeedback("");
    if (this._settleTimer !== null) clearTimeout(this._settleTimer);
    this._settleTimer = null;
    return ++this._responseGeneration;
  },

  _scheduleSettle(responseGeneration) {
    // Late delivery/finally callbacks belong to the request that created them.
    if (responseGeneration !== this._responseGeneration) return;
    if (this._settleTimer !== null) clearTimeout(this._settleTimer);
    const timer = setTimeout(() => {
      if (responseGeneration !== this._responseGeneration || this._settleTimer !== timer) return;
      this._settleTimer = null;
      if (ParticleController._state === undefined || ParticleController._state === "responding") {
        ParticleController._setIdle();
      }
    }, 1000);
    this._settleTimer = timer;
  },

  // ── Form handling ─────────────────────────────────────
  _bindForm() {
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatInput");
    const sendBtn = document.getElementById("sendBtn");

    if (!form || !input || !sendBtn) return;

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    });

    input.addEventListener("input", () => {
      if (this._inputLengthError === null) return;
      const count = Array.from(input.value.trim()).length;
      this._inputLengthError = count > this._maxMessageCharacters ? count : null;
      this._renderInputError();
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (this._sending) return;
      const text = input.value.trim();
      if (!text) return;
      if (!this._validateSubmission(text, input)) return;
      const mode = ParticleController.getConversationMode?.() === "deep" ? "deep" : "normal";
      input.value = "";
      input.style.height = "auto";
      const useStream = document.getElementById("streamToggle")?.checked !== false;
      const rememberToggle = document.getElementById("rememberToggle");
      const remember = rememberToggle?.checked === true;
      if (rememberToggle) rememberToggle.checked = false;

      await this._submit(text, mode, useStream, remember);
    });
  },

  async _submit(text, mode, stream, remember = false) {
    if (this._sending) return false;
    if (!this._validateSubmission(text)) return false;
    this._sending = true;
    const generation = this._beginResponse();
    const button = document.getElementById("sendBtn");
    if (button) { button.disabled = true; button.textContent = "..."; }
    try {
      this._addMessage("user", text, mode);
      ParticleController.setResponding(mode);
      if (stream) await this._sendStream(text, remember, generation, mode);
      else await this._sendViaWS(text, remember, generation, mode);
      return true;
    } finally {
      this._sending = false;
      if (button) {
        button.disabled = false;
        button.textContent = document.getElementById("cubeModel") ? "↑" : this._t("发送", "Send");
      }
      this._renderMessages();
    }
  },

  _t(zh, en) { return typeof I18N !== "undefined" && I18N.lang?.() === "en" ? en : zh; },

  _inputLimitText(count) {
    const length = count.toLocaleString("en-US");
    return this._t(`每条消息最多 4,000 个字符，当前为 ${length} 个。请精简后再发送；完整草稿已保留。`,
      `Messages can contain up to 4,000 characters; this message has ${length}. Shorten it before sending. Your complete draft has been kept.`);
  },

  _validateSubmission(text, input = null) {
    // Pydantic counts Unicode code points; JS string.length counts UTF-16 units.
    const count = Array.from(text).length;
    if (count <= this._maxMessageCharacters) {
      if (input) { this._inputLengthError = null; this._renderInputError(); }
      return true;
    }
    if (input) {
      this._inputLengthError = count;
      this._renderInputError();
      input.focus?.();
    } else this._actionFeedback(this._inputLimitText(count));
    return false;
  },

  _renderInputError() {
    if (typeof document === "undefined") return;
    const input = document.getElementById("chatInput");
    const feedback = document.getElementById("chatInputFeedback");
    const invalid = this._inputLengthError !== null;
    if (invalid) input?.setAttribute?.("aria-invalid", "true");
    else input?.removeAttribute?.("aria-invalid");
    if (feedback) {
      feedback.hidden = !invalid;
      feedback.textContent = invalid ? this._inputLimitText(this._inputLengthError) : "";
    }
  },

  _createReply(text, mode, stream, responseGeneration) {
    const message = { id: ++this._messageSequence, role: "eva", text: "", mode, time: Date.now(),
      status: "submitting", request: { text, mode, stream }, responseGeneration };
    this._messages.push(message);
    this._renderMessages();
    this._renderDeliveryStatus();
    return message;
  },

  _setOutcome(message, status, notice = null, detail = "") {
    message.status = status;
    message.notice = notice;
    message.detail = detail;
    this._renderMessages();
    this._renderDeliveryStatus();
  },

  _applyReceipt(message, result) {
    if (!result || typeof result !== "object" || Array.isArray(result) ||
        (Object.hasOwn(result, "completed") && typeof result.completed !== "boolean") ||
        (!Object.hasOwn(result, "completed") && typeof result.reply !== "string" && !result.terminal_state) ||
        (result.task_id && message.taskId && result.task_id !== message.taskId)) return false;
    if (!message.taskId && typeof result.task_id === "string") message.taskId = result.task_id;
    const uncertain = result.completed === false || result.terminal_state === "outcome_unknown" ||
      ["outcome_unknown", "may_still_be_running"].includes(result.execution_state);
    const status = uncertain ? "unknown" :
      ["rejected", "expired"].includes(result.terminal_state) ? "rejected" :
      result.ok === false || result.terminal_state === "failed" ? "failed" : "completed";
    if (typeof result.reply === "string") message.text = result.reply;
    if (result.mode_info) message.mode_info = result.mode_info;
    // The mode belongs to the submitted request, even if a receipt reports another mode.
    this._setOutcome(message, status, result.reply_available === false ? "reply_missing" : status === "unknown" ? "unknown" :
      status === "failed" || status === "rejected" ? "retry" : null, result.error || "");
    return true;
  },

  _statusLabel(status) {
    const labels = { submitting: ["提交中", "Submitting"], waiting: ["等待回复", "Waiting for reply"],
      receiving: ["接收中", "Receiving"], completed: ["已完成", "Complete"], failed: ["请求失败", "Request failed"],
      rejected: ["未执行", "Not executed"], unknown: ["结果未知", "Outcome unknown"] };
    return labels[status] ? this._t(...labels[status]) : "";
  },

  _noticeText(message) {
    const notices = {
      unknown: ["暂时无法确定结果；请求可能仍在执行。请查询原请求，避免重复提交。", "The outcome is unknown; the request may still be running. Check the original request before sending again."],
      missing: ["未找到这条请求的保留回执，无法确定结果。请勿重复提交。", "No retained receipt was found. The outcome is unknown; avoid sending again."],
      reply_missing: ["已恢复处理回执，原始回复正文未保存。不会自动重发请求。", "The processing receipt was recovered, but the original reply was not retained. The request will not be resent automatically."],
      login: ["请重新登录后查询；请求可能仍在执行。", "Sign in again to check; the request may still be running."],
      retry: ["可显式重新发送：沿用原消息模式，创建独立请求；不会再次记入长期记忆。", "Send again with the original mode as a new request; it will not be added to long-term memory again."],
    };
    let text = notices[message.notice] ? this._t(...notices[message.notice]) : "";
    if (message.detail) text += `${text ? " " : ""}${message.detail}`;
    if (message.status === "unknown" && !message.taskId) text += this._t(" 未取得请求编号，无法查询。", " No request ID was received, so this request cannot be queried.");
    return text;
  },

  _renderDeliveryStatus() {
    if (typeof document === "undefined") return;
    const el = document.getElementById("chatDeliveryStatus");
    const message = [...this._messages].reverse().find(m => m.role === "eva" && m.responseGeneration === this._responseGeneration);
    const statusText = message ? `${this._t("回复状态：", "Reply status: ")}${this._statusLabel(message.status)}` : "";
    if (el && el.textContent !== statusText) el.textContent = statusText;
    const announcement = document.getElementById("chatAnnouncement");
    if (announcement && message && ["completed", "failed", "rejected", "unknown"].includes(message.status)) {
      const text = `${this._statusLabel(message.status)}. ${message.text || ""} ${this._noticeText(message)}`.trim();
      if (announcement.textContent !== text) announcement.textContent = text;
    }
  },

  _bindMessageActions() {
    document.getElementById("chatMessages")?.addEventListener("click", e => {
      const button = e.target.closest("button[data-message-action]");
      if (!button || button.disabled) return;
      if (button.dataset.messageAction === "code") { this._copyCode(button); return; }
      const message = this._messages.find(m => m.id === Number(button.dataset.messageId));
      if (!message) return;
      if (button.dataset.messageAction === "copy") this._copyAnswer(message);
      if (button.dataset.messageAction === "retry") this._retryMessage(message);
      if (button.dataset.messageAction === "query") this._checkResult(message);
    });
  },

  async _copyAnswer(message) {
    if (!message.text || message.text === "...") return false;
    return this._copyText(message.text);
  },

  async _copyText(text) {
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      this._actionFeedback(this._t("已复制", "Copied"));
      return true;
    } catch (_) {
      this._actionFeedback(this._t("复制失败，请选中文字后手动复制。", "Copy failed. Select the text and copy it manually."));
      return false;
    }
  },

  _actionFeedback(text) {
    if (typeof document === "undefined") return;
    const el = document.getElementById("chatActionFeedback");
    if (el) el.textContent = text;
  },

  async _retryMessage(message) {
    if (this._sending || message.retried || !message.request || !["failed", "rejected"].includes(message.status)) return false;
    const { text, mode, stream } = message.request;
    if (!this._validateSubmission(text)) return false;
    message.retried = true;
    // A retry is a new explicit write; never inherit memory consent or the draft.
    return this._submit(text, mode, stream, false);
  },

  async _checkResult(message) {
    if (!message.taskId || message.checking || this._pendingTasks[message.taskId] || !["unknown", "waiting"].includes(message.status)) return;
    message.checking = true;
    this._renderMessages();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    try {
      const response = await EvaHttp.request(`/api/chat/result/${encodeURIComponent(message.taskId)}`, { signal: ctrl.signal });
      const result = await response.json();
      if (["completed", "failed", "rejected"].includes(message.status)) return;
      if (response.ok && result.completed === true && this._applyReceipt(message, result)) this._scheduleSettle(message.responseGeneration);
      else if (response.status === 404) this._setOutcome(message, "unknown", "missing");
      else if (response.ok && result.completed === false && (!result.task_id || result.task_id === message.taskId)) this._setOutcome(message, "waiting");
      else this._setOutcome(message, "unknown", "unknown");
    } catch (error) {
      if (!["completed", "failed", "rejected"].includes(message.status)) this._setOutcome(message, "unknown", error.name === "LoginRequired" ? "login" : "unknown");
    } finally {
      clearTimeout(timer);
      message.checking = false;
      this._renderMessages();
    }
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
  _addMessage(role, text, mode) {
    this._messages.push({ id: ++this._messageSequence, role, text, mode, time: Date.now() });
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

    const nearBottom = container.scrollHeight - container.clientHeight - container.scrollTop < 80;
    const scrollTop = container.scrollTop;
    const focused = document.activeElement;
    const action = container.contains?.(focused) && focused?.dataset?.messageAction;
    const focusedId = action && focused.dataset.messageId;
    const focusedCode = focused?.dataset?.codeIndex;
    const focusedRowId = container.contains?.(focused) && focused?.closest?.(".message-row")?.dataset.messageId;
    container.innerHTML = this._messages.map((m) => {
      if (!m.id) m.id = ++this._messageSequence;
      const isUser = m.role === "user";
      const senderLabel = isUser
        ? (I18N ? I18N.t("chat.you") : "You")
        : (I18N ? I18N.t("chat.eva") : "EVA");
      const timeStr = this._formatTime(m.time);
      const typing = !isUser && (!m.text || m.text === "...") && ["submitting", "waiting", "receiving"].includes(m.status);
      const html = typing ? '<div class="typing-indicator" aria-hidden="true"><span></span><span></span><span></span></div>' :
        isUser ? this._escapeHtml(m.text) : this._renderMarkdown(m.text, m.id);
      const toolCardsHtml = m.toolCards ? this._renderToolCards(m.toolCards) : "";
      const notice = !isUser && this._noticeText(m);
      const canRetry = m.request && ["failed", "rejected"].includes(m.status) && !m.retried;
      const canQuery = m.taskId && ["unknown", "waiting"].includes(m.status) && !this._pendingTasks[m.taskId];
      const button = (action, label, disabled = false) => `<button type="button" class="message-action" data-message-id="${m.id}" data-message-action="${action}"${disabled ? " disabled" : ""}>${label}</button>`;
      return `<div class="message-row ${isUser ? "user" : "eva"}" data-message-id="${m.id}" tabindex="-1">
        <span class="message-sender">${senderLabel}${this._modeLabel(m.mode)}${isUser ? "" : this._modeInfoLabel(m.mode_info)}</span>
        ${toolCardsHtml}
        ${html ? `<div class="message-bubble">${html}</div>` : ""}
        ${notice ? `<p class="message-notice">${this._escapeHtml(notice)}</p>` : ""}
        ${!isUser && m.taskId ? `<details class="message-receipt"><summary>${this._t("请求编号", "Request ID")}</summary><code>${this._escapeHtml(m.taskId)}</code></details>` : ""}
        <div class="message-footer"><span class="message-time">${timeStr}</span>
          ${!isUser && m.status ? `<span class="message-status ${m.status}">${this._statusLabel(m.status)}</span>` : ""}
          ${!isUser && m.text && m.text !== "..." ? button("copy", ["receiving", "unknown", "waiting"].includes(m.status) ? this._t("复制已有内容", "Copy received text") : this._t("复制回答", "Copy answer")) : ""}
          ${canRetry ? button("retry", this._t("重新发送", "Send again"), this._sending) : ""}
          ${canQuery ? button("query", m.checking ? this._t("查询中…", "Checking…") : this._t("查询结果", "Check result"), m.checking) : ""}
          ${m.retried ? `<span class="message-time">${this._t("已作为新请求重发", "Sent again as a new request")}</span>` : ""}
        </div>
      </div>`;
    }).join("");

    // Keep keyboard focus and reading position through streaming updates.
    if (action && focusedId) {
      const replacement = container.querySelector(`button[data-message-id="${Number(focusedId)}"][data-message-action="${action}"]${focusedCode !== undefined ? `[data-code-index="${Number(focusedCode)}"]` : ""}`);
      const target = replacement && !replacement.disabled ? replacement : container.querySelector(`.message-row[data-message-id="${Number(focusedId)}"]`);
      target?.focus({ preventScroll: true });
    } else if (focusedRowId) container.querySelector(`.message-row[data-message-id="${Number(focusedRowId)}"]`)?.focus({ preventScroll: true });
    container.scrollTop = nearBottom ? container.scrollHeight : scrollTop;
    globalThis.EvaChatReading?.init(globalThis)?.update({follow:nearBottom});
  },

  _rerenderMessages() {
    this._renderMessages();
  },

  _modeLabel(mode) {
    if (mode !== "normal" && mode !== "deep") return "";
    const en = typeof I18N !== "undefined" && I18N.lang() === "en";
    const label = mode === "deep" ? (en ? "Deep thinking" : "深度思考") : (en ? "Conversation" : "正常对话");
    return `<span class="message-mode"> · ${label}</span>`;
  },

  _modeInfoLabel(info) {
    const strategy = info && info.strategy;
    if (!strategy) return "";
    const en = typeof I18N !== "undefined" && I18N.lang() === "en";
    const labels = en
      ? { native: "native reasoning", prompt: "answer policy", mock: "demo", preview: "preview" }
      : { native: "原生推理", prompt: "回答策略", mock: "演示", preview: "预览" };
    const label = labels[strategy] || strategy;
    return `<span class="message-strategy" title="${this._escapeHtml(strategy)}"> · ${this._escapeHtml(label)}</span>`;
  },

  _escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  },

  // Full markdown → HTML for LLM replies (ChatGPT-style output)
  _renderMarkdown(s, messageId = 0) {
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
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) =>
        /^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(url)
          ? `<a href="${url}" target="_blank" rel="noopener">${label}</a>` : label);

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
          `<div class="chat-code-wrap">${langLabel}<button type="button" class="code-copy-btn" data-message-id="${messageId}" data-code-index="${idx}" data-message-action="code">${this._t("复制代码", "Copy code")}</button>`
          + `<pre class="chat-code-block"><code>${this._escapeHtml(f.code)}</code></pre></div>`
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

  async _copyCode(btn) {
    const code = btn.closest(".chat-code-wrap")?.querySelector("code");
    if (!code) return;
    return this._copyText(code.textContent);
  },

  // ── WebSocket sync mode ───────────────────────────────
  async _sendViaWS(text, remember = false, responseGeneration = this._responseGeneration, mode = "normal") {
    const message = this._createReply(text, mode, false, responseGeneration);

    try {
      const r = await EvaHttp.request("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, remember, mode }),
      });
      const ack = await r.json();
      if (ack.accepted === false) {
        this._setOutcome(message, "rejected", "retry", ack.detail || ack.error || "");
        return;
      }
      if (!r.ok || ack.accepted !== true) throw new Error("Unconfirmed admission");
      const taskId = ack.task_id;
      if (!taskId) throw new Error("No task_id returned");
      message.taskId = taskId;

      // Register pending task — reply arrives via WS chat_reply
      this._pendingTasks[taskId] = {
        mode: "sync",
        fullReply: "",
        responseGeneration,
        message,
      };
      this._setOutcome(message, "waiting");
      // Poll immediately: a fast WS reply can arrive before the HTTP ack.
      await this._pollTask(taskId, Date.now() + ((ack.request_deadline_sec || 300) + 5) * 1000);
    } catch (err) {
      this._setOutcome(message, "unknown", err.name === "LoginRequired" ? "login" : "unknown");
    } finally {
      this._scheduleSettle(responseGeneration);
    }
  },

  async _pollTask(taskId, until) {
    while (this._pendingTasks[taskId]) {
      const message = this._pendingTasks[taskId].message;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 5000);
      try {
        const response = await EvaHttp.request(`/api/chat/result/${encodeURIComponent(taskId)}`, { signal: ctrl.signal });
        const result = await response.json();
        if (response.ok && result.completed === true && (!result.task_id || result.task_id === taskId)) {
          result.task_id = taskId;
          this._onWSMessage({ channel: "chat_reply", payload: result });
          return;
        }
        if (response.status === 404) {
          this._endWaiting(taskId, message, "missing");
          return;
        }
      } catch (error) {
        if (error.name === "LoginRequired") {
          this._endWaiting(taskId, message, "login");
          return;
        }
        // Retry delivery without resubmitting the action.
      }
      finally { clearTimeout(timer); }
      if (Date.now() >= until) {
        this._endWaiting(taskId, message, "unknown");
        return;
      }
      if (this._pendingTasks[taskId]) await new Promise(resolve => setTimeout(resolve, 1000));
    }
  },

  _endWaiting(taskId, message, notice) {
    // A simultaneous terminal WebSocket delivery wins over a stale GET failure.
    if (!this._pendingTasks[taskId]) return;
    delete this._pendingTasks[taskId];
    this._setOutcome(message, "unknown", notice);
    this._scheduleSettle(message.responseGeneration);
  },

  // ── SSE Streaming (unified pipeline via /api/chat/stream) ──
  async _sendStream(text, remember = false, responseGeneration = this._responseGeneration, mode = "normal") {
    if (this._streamAbort) this._streamAbort.abort();
    const ctrl = new AbortController();
    this._streamAbort = ctrl;

    const message = this._createReply(text, mode, true, responseGeneration);
    let reader;
    let terminal = false;

    try {
      const r = await EvaHttp.request("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, remember, mode }),
        signal: ctrl.signal,
      });
      if (!r.ok) {
        const rejected = await r.json();
        if (rejected.accepted === false) {
          this._setOutcome(message, "rejected", "retry", rejected.detail || rejected.error || "");
          return;
        }
        throw new Error("Unconfirmed admission");
      }

      message.taskId = r.headers?.get("X-EVA-Task-ID") || null;
      this._setOutcome(message, "waiting");
      reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let full = "";
      let eventType = "message";
      let eventData = [];
      let ended = false;
      let toolCards = [];  // accumulated tool progress cards

      // Regex for tool progress events
      const TOOL_RE = /^\[(TOOL_START|TOOL_RESULT|TOOL_ERROR|TOOL_MAX_ROUNDS):([^\]]+)\]\s*(.*)$/;

      const deliver = () => {
        if (!eventData.length || terminal || ended) return;
        const data = eventData.join("\n");
        if (eventType === "result") {
          const result = JSON.parse(data);
          if (this._applyReceipt(message, result)) {
            full = message.text;
            terminal = true;
          }
          return;
        }
        if (data === "[DONE]" || data === "[ERROR]" || data.startsWith("[ERROR:")) { ended = true; return; }
        const toolMatch = data.trim().match(TOOL_RE);
        if (toolMatch) {
          const [, eventType, toolName, detail] = toolMatch;
          toolCards.push({ eventType, toolName, detail: detail.trim() });
        } else {
          full += data;
          ParticleController.burstParticles(2);
        }
        message.text = full;
        message.toolCards = toolCards.slice();
        this._setOutcome(message, "receiving");
      };
      while (!ended && !terminal) {
        const { done, value } = await reader.read();
        if (["completed", "failed", "rejected"].includes(message.status)) { terminal = true; break; }
        buf += done ? decoder.decode() : decoder.decode(value, { stream: true });
        if (done) buf += "\n\n";
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, "");
          if (line === "") { deliver(); eventData = []; eventType = "message"; }
          else if (line.startsWith("event:")) eventType = line.slice(6).trim();
          else if (line.startsWith("data:")) eventData.push(line.slice(5).replace(/^ /, ""));
        }
        if (done) break;
      }
      if (!terminal) this._setOutcome(message, "unknown", "unknown");
    } catch (err) {
      if (!terminal && !["completed", "failed", "rejected"].includes(message.status)) this._setOutcome(message, "unknown", err.name === "LoginRequired" ? "login" : "unknown");
    } finally {
      // Stop local delivery only. The admitted server request is not cancelled.
      try { await reader?.cancel?.(); } catch (_) { /* terminal state already recorded */ }
      try { reader?.releaseLock?.(); } catch (_) { /* delivery cleanup must not block the composer */ }
      if (this._streamAbort === ctrl) this._streamAbort = null;
      this._scheduleSettle(responseGeneration);
    }
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
      TOOL_START:   this._t("执行中", "Running"),
      TOOL_RESULT:  this._t("已完成", "Done"),
      TOOL_ERROR:   this._t("失败", "Failed"),
      TOOL_MAX_ROUNDS: this._t("已达上限", "Limit"),
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
// Chat owns its delivery socket; theme/language do not need a second socket.
Nav._bindTheme();
Nav._bindLang();
ParticleController.init("avatarWrapper", "particleCanvas");
Chat.init();
