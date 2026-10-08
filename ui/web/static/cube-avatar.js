// Order-aware CSS 3D presentation. Keep the existing chat/avatar contract.
const _brandGeometry = EvaBrand.tokens.geometry;
const CubePresentation = Object.freeze({
  hero: [..._brandGeometry.heroAngle],
  perspective: _brandGeometry.perspective,
  hoverX: 2, hoverY: 3,
});
const CubeModes = EvaBrand.tokens.motion.modes;
const ParticleController = {
  _state: "idle",
  _phase: 0,
  _requestedPhase: 0,
  _manualPhase: 0,
  _playing: false,
  _continuousPaused: false,
  _dragging: false,
  _timer: null,
  _phaseRequest: 0,
  _exitPromise: null,
  _motionFault: null,
  _recoveryPromise: null,
  _order: 2,
  _requestedOrder: 2,
  _selectedMode: "normal",
  _activeResponseMode: null,
  _modeCapabilities: {},
  _modeCapabilityError: false,
  _angles: [...CubePresentation.hero],
  _cubies: [],
  _views: EvaBrand.tokens.geometry.views,
  _descriptions: [
    ["八个独立模块，一个完整的思考起点。", "Eight independent modules. One starting point."],
    ["旋转一个角度，打开一种新的理解。", "Turn a layer. Open a new perspective."],
    ["拆解复杂问题，看见每个独立的可能。", "Break down complexity into individual possibilities."],
    ["重新连接，让想法形成新的秩序。", "Reconnect the pieces. Discover a new order."],
    ["从一个单元出发，让可能向外延展。", "Start with a single unit. Expand what is possible."],
    ["在变化中探索，回到完整的自己。", "Explore new possibilities. Return to a whole."],
  ],

  init() {
    this._model = document.getElementById("cubeModel");
    this._stage = document.getElementById("cubeStage");
    this._rotor = document.getElementById("cubeRotor");
    this._presence = document.getElementById("cubePresence");
    if (!this._model || !this._stage) return;
    globalThis.EvaBrand?.applyCss(document.documentElement,
      document.documentElement.getAttribute("data-theme") || "light");
    this._stage.style.setProperty("--hero-x", `${CubePresentation.hero[0]}deg`);
    this._stage.style.setProperty("--hero-y", `${CubePresentation.hero[1]}deg`);
    this._stage.style.setProperty("--cube-perspective", `${CubePresentation.perspective}px`);
    const inspector = document.getElementById("cubeInspector");
    this._stage.dataset.debug = String(Boolean(inspector?.open));
    inspector?.addEventListener("toggle", () => { this._stage.dataset.debug = String(inspector.open); });
    this._motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    this._mountCube(2);
    document.getElementById("cubeRetry")?.addEventListener("click", () => this._retryLogo());
    document.querySelectorAll("[data-cube-order]").forEach(button => button.addEventListener("click", () =>
      this.setConversationMode(Number(button.dataset.cubeOrder) === 3 ? "deep" : "normal")));
    document.querySelectorAll("[data-chat-mode]").forEach(button => button.addEventListener("click", () =>
      this.setConversationMode(button.dataset.chatMode)));
    document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => this._view(button.dataset.view)));
    document.querySelectorAll("[data-phase]").forEach(button => button.addEventListener("click", () => {
      this._stopPlayback();
      this._manualPhase = Number(button.dataset.phase);
      this._continuousPaused = false;
      this._setPhase(this._manualPhase);
    }));
    document.getElementById("resetCube").addEventListener("click", async () => {
      this._stopPlayback();
      this._manualPhase = 0;
      this._continuousPaused = false;
      await this._view("iso", true);
    });
    document.getElementById("playEvolution").addEventListener("click", () => {
      if (this._phase === 5) {
        this._continuousPaused = !this._continuousPaused;
        this._syncMotion();
      } else if (this._playing) this._stopPlayback();
      else {
        this._playing = true;
        this._setPhase(0);
        this._updatePlayback();
        this._schedule();
      }
    });
    document.querySelectorAll("[data-prompt-zh]").forEach(button => button.addEventListener("click", () => {
      const input = document.getElementById("chatInput");
      const prompt = this._english() ? button.dataset.promptEn : button.dataset.promptZh;
      const draft = input.value || "";
      const next = draft.trim() ? (draft.trimEnd().endsWith(prompt) ? draft : `${draft}\n\n${prompt}`) : prompt;
      const limit = input.maxLength > 0 ? input.maxLength : 32000;
      if (next.length > limit) { this._promptFeedback("limit"); input.focus(); return; }
      input.value = next;
      this._promptFeedback("inserted");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }));
    this._bindRotation();
    window.addEventListener("lang-changed", () => this._translate());
    document.addEventListener("visibilitychange", () => {
      clearTimeout(this._timer);
      this._syncMotion();
      if (!document.hidden) this._schedule();
    });
    this._motion.addEventListener("change", () => {
      clearTimeout(this._timer);
      this._resetHover();
      if (this._motion.matches) this._stopPlayback();
      else this._schedule();
      const logo = this._logo;
      logo.setReducedMotion(this._motion.matches).then(() => {
        if (logo !== this._logo) return;
        this._syncMotion();
        if (this._phase === 5 && !this._motion.matches && !this._exitPromise) this._startLogo();
      }).catch(error => this._logoError(error, logo));
      this._syncMotion();
    });
    this._setPhase(0);
    this._setIdle();
    this._translate();
  },

  _english() { return typeof I18N !== "undefined" && I18N.lang() === "en"; },

  _mountCube(order) {
    // Called only after the previous model has finished its inverse journal.
    this._rotor.replaceChildren();
    this._order = order;
    const state = new CubeLogic.CubeState(order);
    const mode = CubeModes[order === 3 ? "deep" : "normal"];
    this._mountRenderer(state);
    this._logoStatus = null;
    this._stage.dataset.order = String(order);
    this._stage.dataset.logoState = "idle";
    this._stage.dataset.solved = "true";
    this._stage.dataset.move = "";
    this._stage.dataset.cycles = "0";
    this._logo = new CubeLogoSystem.CubeLogo({ ...mode, state, animator: this._renderer, loop: true,
      onChange: status => { if (this._logo?.state === state) this._onLogoChange(status); } });
    const logo = this._logo;
    logo.setReducedMotion(this._motion.matches).catch(error => this._logoError(error, logo));
  },

  _mountRenderer(state) {
    this._rotor.replaceChildren();
    this._renderer = new CubeRendering.CubeAnimator(this._rotor, state);
    this._geometry = this._renderer.geometry;
    // Presentation poses use master positions, even when mounting a scrambled state.
    this._cubies = new CubeLogic.CubeState(state.order).cubies.map(cubie => ({
      node: this._renderer.nodes.get(cubie.id), x: cubie.position[0], y: cubie.position[1], z: cubie.position[2],
    }));
    return this._renderer;
  },

  setConversationMode(mode) {
    if (!CubeModes[mode] || !this._model) return Promise.resolve();
    this._selectedMode = mode;
    if (this._activeResponseMode && this._state === "responding") {
      this._updateModes();
      return Promise.resolve();
    }
    this._stopPlayback();
    this._continuousPaused = false;
    const phase = this._state === "responding" ? (this._motion.matches ? 0 : 5) : this._requestedPhase;
    return this._setPhase(phase, CubeModes[mode].order);
  },

  getConversationMode() { return this._selectedMode; },

  setModeCapabilities(modes) {
    this._modeCapabilityError = false;
    this._modeCapabilities = {};
    if (Array.isArray(modes)) modes.forEach(item => {
      if (item && CubeModes[item.mode]) this._modeCapabilities[item.mode] = item;
    });
    this._updateModes();
  },

  setModeCapabilityError() {
    this._modeCapabilityError = true;
    this._updateModes();
  },

  _updateModes() {
    const deep = this._selectedMode === "deep";
    document.querySelectorAll("[data-cube-order]").forEach(button =>
      button.setAttribute("aria-pressed", String(Number(button.dataset.cubeOrder) === (deep ? 3 : 2))));
    document.querySelectorAll("[data-chat-mode]").forEach(button =>
      button.setAttribute("aria-pressed", String(button.dataset.chatMode === (deep ? "deep" : "normal"))));
    const modelName = this._english() ? `${this._order} × ${this._order} cube` : `${this._order === 3 ? "三" : "二"}阶魔方`;
    this._stage.setAttribute("aria-label", this._english()
      ? `Interactive ${modelName}. Drag or use arrow keys to rotate. Home resets the view.`
      : `交互式${modelName}。拖动或使用方向键旋转，Home 键复位。`);
    const badge = document.getElementById("cubeModelLabel");
    if (badge) badge.textContent = this._english()
      ? (this._order === 3 ? "3 × 3 · DEEP THINKING" : "2 × 2 · CONVERSATION")
      : (this._order === 3 ? "3 × 3 · 深度思考" : "2 × 2 · 正常对话");
    const queued = this._activeResponseMode && this._activeResponseMode !== this._selectedMode;
    const status = document.getElementById("cubeModeStatus");
    if (status) status.textContent = this._exitPromise
      ? (this._english() ? "Restoring the current cube…" : "正在复原当前魔方…")
      : queued
        ? (this._english()
          ? `This reply: ${this._activeResponseMode === "deep" ? "3 × 3 deep thinking" : "2 × 2 conversation"}; next: ${deep ? "3 × 3 deep thinking" : "2 × 2 conversation"}`
          : `本条：${this._activeResponseMode === "deep" ? "三阶深度思考" : "二阶正常对话"}；下一条：${deep ? "三阶深度思考" : "二阶正常对话"}`)
      : (this._english() ? (deep ? "Deep thinking · 3 × 3 animation" : "Conversation · 2 × 2 animation")
        : (deep ? "深度思考 · 三阶动效" : "正常对话 · 二阶动效"));
    const hint = document.getElementById("chatModeHint");
    if (hint) {
      const en = this._english(), capability = this._modeCapabilities[this._selectedMode];
      hint.textContent = queued
        ? (en ? `This reply: ${this._activeResponseMode === "deep" ? "deep thinking" : "conversation"}. Selection applies to the next message.`
          : `本条：${this._activeResponseMode === "deep" ? "深度思考" : "正常对话"}；新选择用于下一条消息。`)
        : this._modeCapabilityError
          ? (en ? "Capability details unavailable. The selected mode is still sent with the message."
            : "暂时无法读取模型能力；所选模式仍会随消息发送。")
        : capability?.strategy === "mock" || capability?.strategy === "preview"
          ? (en ? "Preview mode · No real model call." : "演示模式 · 不调用真实模型。")
          : deep && capability?.strategy === "native"
            ? (en ? "More reasoning for complex questions. Replies may take longer." : "增加推理投入，适合复杂问题，回复可能稍慢。")
            : deep && capability?.strategy === "prompt"
              ? (en ? "More analysis and checking through the model’s answer instructions." : "通过回答策略加强分析与核对。")
              : (en ? "Your selected mode is sent with each message." : "所选模式随每条消息发送。");
    }
    this._updateLabel();
  },

  _translate() {
    this._promptFeedback();
    document.querySelectorAll("[data-zh][data-en]").forEach(node => { node.textContent = this._english() ? node.dataset.en : node.dataset.zh; });
    document.getElementById("chatInput").placeholder = this._english() ? "Share what’s on your mind…" : "说说你的想法…";
    const send = document.getElementById("sendBtn");
    send.setAttribute("aria-label", this._english() ? "Send message" : "发送消息");
    send.title = send.getAttribute("aria-label");
    this._describePhase();
    this._updateLabel();
    this._updatePlayback();
    this._updateModes();
  },

  _promptFeedback(kind) {
    const notice = document.getElementById("promptFeedback");
    if (!notice) return;
    if (kind) notice.dataset.feedback = kind;
    const current = notice.dataset.feedback;
    if (!current) return;
    notice.hidden = false;
    notice.textContent = current === "limit"
      ? (this._english() ? "Shorten your draft before adding this suggestion." : "请先精简草稿，再添加这条建议。")
      : (this._english() ? "Suggestion added to your draft. Nothing has been sent." : "建议已加入草稿，尚未发送。") ;
  },

  async _view(name, resetShape = false) {
    this._resetHover();
    // A fixed view must stay still so its label reflects the visible orientation.
    if (this._phase === 5 || resetShape) {
      this._stopPlayback();
      this._manualPhase = 0;
      const restoring = this._setPhase(0);
      const request = this._phaseRequest;
      await restoring;
      if (request !== this._phaseRequest) return;
    }
    this._angles = [...this._views[name]];
    this._rotate();
    document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === name)));
  },

  _rotate() {
    this._model.style.transform = `rotateX(${this._angles[0]}deg) rotateY(${this._angles[1]}deg)`;
  },

  _bindRotation() {
    let drag = null;
    let bounds = null;
    this._stage.addEventListener("pointerenter", () => { bounds = this._stage.getBoundingClientRect(); });
    this._stage.addEventListener("pointerleave", () => { bounds = null; this._resetHover(); });
    this._stage.addEventListener("pointerdown", event => {
      if (event.button !== 0 || drag) return;
      this._resetHover();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, angles: [...this._angles] };
      this._dragging = true;
      this._syncMotion();
      this._stage.setPointerCapture(event.pointerId);
      this._stage.classList.add("dragging");
    });
    this._stage.addEventListener("pointermove", event => {
      if (!drag) {
        if (event.pointerType !== "mouse" || !bounds || !this._presence || this._phase !== 0 || this._state !== "idle" || this._logo.busy || this._motion.matches) return;
        const unit = (position, start, size) => Math.max(-1, Math.min(1, (position - start) / size * 2 - 1));
        this._presence.style.setProperty("--hover-x", `${(-unit(event.clientY, bounds.top, bounds.height) * CubePresentation.hoverX).toFixed(2)}deg`);
        this._presence.style.setProperty("--hover-y", `${(unit(event.clientX, bounds.left, bounds.width) * CubePresentation.hoverY).toFixed(2)}deg`);
        return;
      }
      if (drag.id !== event.pointerId) return;
      this._angles = [drag.angles[0] - (event.clientY - drag.y) * .45, drag.angles[1] + (event.clientX - drag.x) * .45];
      this._rotate();
      this._clearViewSelection();
    });
    const release = () => {
      drag = null;
      this._dragging = false;
      this._stage.classList.remove("dragging");
      this._syncMotion();
    };
    this._stage.addEventListener("pointerup", release);
    this._stage.addEventListener("pointercancel", release);
    this._stage.addEventListener("lostpointercapture", release);
    this._stage.addEventListener("keydown", event => {
      this._resetHover();
      if (event.key === "Home") { event.preventDefault(); this._view("iso"); return; }
      const delta = { ArrowUp: [-15, 0], ArrowDown: [15, 0], ArrowLeft: [0, -15], ArrowRight: [0, 15] }[event.key];
      if (!delta) return;
      event.preventDefault();
      this._angles = this._angles.map((angle, index) => angle + delta[index]);
      this._rotate();
      this._clearViewSelection();
    });
  },

  _resetHover() {
    this._presence?.style.setProperty("--hover-x", "0deg");
    this._presence?.style.setProperty("--hover-y", "0deg");
  },

  _clearViewSelection() {
    document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", "false"));
  },

  _setPhase(phase, order = this._requestedOrder) {
    const request = ++this._phaseRequest;
    this._requestedPhase = phase;
    this._requestedOrder = order;
    if (this._motionFault) {
      this._updateModes();
      this._describePhase();
      return this._recoveryPromise || Promise.resolve();
    }
    if (phase === 5 && this._phase === 5 && order === this._order && !this._exitPromise) {
      this._syncMotion();
      this._updateModes();
      this._startLogo();
      return Promise.resolve();
    }
    if (this._logo.busy || !this._logo.state.isSolved() || this._exitPromise) {
      // Finish the active turn and undo only committed moves before changing geometry.
      const logo = this._logo;
      if (!this._exitPromise) this._exitPromise = logo.solve();
      this._syncMotion();
      this._updateModes();
      const restoring = this._exitPromise;
      return restoring.then(() => {
        if (this._exitPromise === restoring) this._exitPromise = null;
        if (request === this._phaseRequest) this._applyPhase(phase, order);
      }).catch(error => {
        if (this._exitPromise === restoring) this._exitPromise = null;
        this._logoError(error, logo);
      });
    }
    this._applyPhase(phase, order);
    return Promise.resolve();
  },

  _applyPhase(phase, order = this._requestedOrder) {
    this._resetHover();
    if (order !== this._order) this._mountCube(order);
    this._phase = phase;
    this._stage.dataset.phase = String(phase);
    const spacing = this._geometry.spacing;
    if (phase === 5) this._renderer.render(this._logo.state);
    else this._cubies.forEach(({ node, x, y, z }, index) => {
      let px = x * spacing, py = y * spacing, pz = z * spacing, turn = 0, tilt = 0;
      if (phase === 1 && y < 0) {
        // Rotate the entire top layer about its own center, including its positions.
        const angle = Math.PI / 4;
        px = (x * Math.cos(angle) + z * Math.sin(angle)) * spacing;
        pz = (-x * Math.sin(angle) + z * Math.cos(angle)) * spacing;
        turn = 45;
      } else if (phase === 2) {
        px = x * spacing * 1.8; py = y * spacing * 1.8; pz = z * spacing * 1.8;
      } else if (phase === 3) {
        // A permuted 2x2x2 arrangement: every module has a distinct destination.
        px = z * spacing; py = -x * spacing; pz = -y * spacing;
        turn = 90; tilt = 90;
      } else if (phase === 4) {
        // Each depth plane extends along the same open-center perimeter.
        const ring = [[-1,-1],[0,-1],[1,-1],[1,0],[1,1],[0,1],[-1,1],[-1,0]];
        if (this._order === 2) {
          px = ring[index][0] * spacing * 2; py = ring[index][1] * spacing * 2; pz = 0;
        } else {
          // Keep all 26 modules separate: three rings plus the two face centers.
          px = x * spacing * 1.25; py = y * spacing * 1.25; pz = z * spacing * 1.6;
          if (x === 0 && y === 0) { px = z * spacing * 2.5; py = 0; pz = 0; }
        }
        turn = 90;
      }
      node.style.transform = `translate3d(${px}px, ${py}px, ${pz}px) rotateY(${turn}deg) rotateX(${tilt}deg)`;
    });
    document.querySelectorAll("[data-phase]").forEach(button => button.setAttribute("aria-pressed", String(Number(button.dataset.phase) === phase)));
    document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(
      phase !== 5 && this._views[button.dataset.view].every((angle, index) => angle === this._angles[index])
    )));
    this._syncMotion();
    this._describePhase();
    this._updateModes();
    if (phase === 5) this._startLogo();
  },

  _startLogo() {
    if (this._motionFault || this._motion.matches || this._exitPromise || this._logo.busy) return;
    this._resetHover();
    const logo = this._logo;
    logo.play().catch(error => this._logoError(error, logo));
  },

  _onLogoChange(status) {
    if (status.stage === "settling" && this._logoStatus?.stage !== "settling" && this._phase === 5 && !this._dragging) {
      this._angles = [...CubePresentation.hero];
      this._rotate();
    }
    this._logoStatus = status;
    this._stage.dataset.logoState = this._motionFault ? (this._recoveryPromise ? "recovering" : "error") : status.stage;
    this._stage.dataset.solved = String(status.solved);
    this._stage.dataset.move = status.move;
    this._stage.dataset.cycles = String(status.completedCycles);
    this._describePhase();
  },

  _logoError(error, logo = this._logo) {
    if (logo !== this._logo) return;
    this._motionFault = error;
    this._resetHover();
    this._stopPlayback();
    this._stage.dataset.motion = "paused";
    this._stage.dataset.logoState = "error";
    this._stage.dataset.fallback = "true";
    const image = document.getElementById("cubeFallback");
    if (image) {
      image.hidden = false;
      image.src = EvaBrandAssets.dataUrl({order:this._order});
    }
    console.error("Cube logo animation stopped:", error);
    this._describePhase();
    this._updatePlayback();
  },

  _retryLogo() {
    if (this._recoveryPromise) return this._recoveryPromise;
    if (!this._motionFault) return Promise.resolve();
    const logo = this._logo;
    this._recoveryPromise = logo.recover({createAnimator: state => this._mountRenderer(state)})
      .then(() => {
        if (logo !== this._logo) return;
        this._motionFault = null;
        this._stage.dataset.fallback = "false";
        const image = document.getElementById("cubeFallback");
        if (image) image.hidden = true;
        this._applyPhase(this._requestedPhase, this._requestedOrder);
      }).catch(error => this._logoError(error, logo)).finally(() => {
        this._recoveryPromise = null;
        this._syncMotion();
        this._describePhase();
      });
    this._stage.dataset.logoState = "recovering";
    this._describePhase();
    this._updatePlayback();
    return this._recoveryPromise;
  },

  _syncMotion() {
    if (!this._stage) return;
    this._stage.dataset.motion = this._motionFault ? "paused" : this._phase !== 5 ? "off"
      : ((this._continuousPaused && !this._exitPromise) || document.hidden || this._motion.matches || this._dragging ? "paused" : "running");
    if (this._logo) {
      for (const [reason, paused] of [["user", this._continuousPaused && this._phase === 5 && !this._exitPromise], ["hidden", document.hidden], ["drag", this._dragging]]) {
        if (paused) this._logo.pause(reason);
        else this._logo.resume(reason);
      }
    }
    this._updatePlayback();
  },

  _describePhase() {
    const errorPanel = document.getElementById("cubeRecovery");
    if (errorPanel) errorPanel.hidden = !this._motionFault;
    const retry = document.getElementById("cubeRetry");
    if (retry) {
      retry.disabled = Boolean(this._recoveryPromise);
      retry.textContent = this._english() ? (this._recoveryPromise ? "Restoring…" : "Retry animation")
        : (this._recoveryPromise ? "正在恢复…" : "重试动效");
    }
    const errorNote = document.getElementById("cubeRecoveryNote");
    if (errorNote) errorNote.textContent = this._english()
      ? "Animation stopped. A static mark is shown; your conversation can continue."
      : "动效已停止，暂用静态标识；对话仍可继续。";
    const label = document.getElementById("phaseDescription");
    if (label) label.textContent = this._descriptions[this._phase][this._english() ? 1 : 0];
    if (label && this._phase === 0 && this._order === 3) label.textContent = this._english()
      ? "Twenty-six connected modules. More room to explore." : "二十六个独立模块，容纳更深入的思考。";
    if (label && this._motionFault) label.textContent = this._english()
      ? (this._recoveryPromise ? "Restoring committed moves before resuming." : "Animation stopped. The move journal is preserved.")
      : (this._recoveryPromise ? "正在按已提交的动作记录逆序复原。" : "动效已停止，动作记录已保留。");
    const output = document.getElementById("cubeMoveStatus");
    if (!output) return;
    output.hidden = this._phase !== 5;
    const status = this._logoStatus;
    if (!status) return;
    if (this._motionFault) {
      output.textContent = this._english() ? "Animation stopped · journal preserved" : "动效中断 · 动作记录已保留";
      return;
    }
    const names = this._english()
      ? { idle: "Solved", intro: "Ready", scrambling: "Scrambling", scrambled: "Scrambled", solving: "Restoring", settling: "Solved", error: "Stopped" }
      : { idle: "已复原", intro: "准备", scrambling: "打乱", scrambled: "已打乱", solving: "逆序复原", settling: "已复原", error: "已停止" };
    output.textContent = (this._motion.matches ? (this._english() ? "Reduced motion · static logo" : "减少动态效果 · 静态标识") : names[status.stage])
      + (status.move && !this._motion.matches ? ` · ${status.index}/${status.total} · ${status.move}` : "");
    output.title = `${this._english() ? "Scramble" : "打乱"}: ${status.scrambleMoves.join(" ")}\n${this._english() ? "Inverse" : "复原"}: ${status.solveMoves.join(" ")}`;
  },

  _schedule() {
    clearTimeout(this._timer);
    if (document.hidden || !this._playing || this._motion.matches) return;
    this._timer = setTimeout(() => {
      const next = this._phase + 1;
      if (next === 5) {
        this._manualPhase = 5;
        this._continuousPaused = false;
        this._stopPlayback();
        this._setPhase(5);
        return;
      }
      this._setPhase(next);
      this._schedule();
    }, 2400);
  },

  _stopPlayback() {
    this._playing = false;
    clearTimeout(this._timer);
    this._updatePlayback();
  },

  _updatePlayback() {
    const button = document.getElementById("playEvolution");
    if (!button) return;
    const continuous = this._phase === 5;
    const active = continuous ? this._stage.dataset.motion === "running" : this._playing;
    button.disabled = Boolean(this._motionFault) || this._motion.matches || Boolean(this._exitPromise);
    button.title = this._motion.matches ? (this._english() ? "Reduced motion is enabled" : "系统已启用减少动态效果") : "";
    button.setAttribute("aria-pressed", String(active));
    button.querySelector(".play-glyph").textContent = active ? "Ⅱ" : "▷";
    document.getElementById("playLabel").textContent = this._english()
      ? (continuous ? (active ? "Pause rotation" : "Resume rotation") : (active ? "Pause" : "Play sequence"))
      : (continuous ? (active ? "暂停旋转" : "继续旋转") : (active ? "暂停演变" : "播放演变"));
    if (this._exitPromise) document.getElementById("playLabel").textContent = this._english() ? "Restoring" : "复原中";
  },

  _updateLabel() {
    const label = document.getElementById("avatarStateLabel");
    const key = `avatar.${this._state}`;
    if (this._state === "responding" && this._order === 3) {
      delete label.dataset.i18n;
      label.textContent = this._english() ? "EVA · Deep thinking" : "EVA · 深度思考中";
    } else {
      label.dataset.i18n = key;
      label.textContent = I18N.t(key);
    }
    this._stage.dataset.state = this._state;
  },

  _setIdle() {
    if (!this._model) return;
    this._state = "idle";
    this._activeResponseMode = null;
    if (!this._playing) { clearTimeout(this._timer); this._setPhase(this._manualPhase, CubeModes[this._selectedMode].order); }
    this._updateLabel();
    this._updateModes();
  },

  setListening() {
    if (!this._model || this._state === "responding") return;
    this._state = "listening";
    if (!this._playing && this._phase !== 5 && !this._motion.matches) this._setPhase(1);
    this._updateLabel();
  },

  setResponding(mode = this._selectedMode) {
    if (!this._model) return;
    if (!CubeModes[mode]) mode = "normal";
    this._activeResponseMode = mode;
    this._state = "responding";
    this._stopPlayback();
    this._setPhase(this._motion.matches ? 0 : 5, CubeModes[mode].order);
    this._updateLabel();
    this._schedule();
  },

  // Tokens already share the responding animation; avoid restarting transitions per token.
  burstParticles() {},
};
