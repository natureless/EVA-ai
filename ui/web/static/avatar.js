// EVA Avatar — SVG female character + zoom state machine + particle canvas
const AvatarController = {
  _state: "idle",
  _wrapper: null,
  _svg: null,
  _mouthPath: null,
  _leftEye: null,
  _rightEye: null,
  _particleCanvas: null,
  _particles: [],
  _particleRAF: null,
  _mouthTimer: null,
  _blinkTimer: null,
  _breatheTimer: null,

  init(containerId, canvasId) {
    this._wrapper = document.getElementById(containerId);
    this._particleCanvas = document.getElementById(canvasId);
    if (!this._wrapper) return;

    this._wrapper.innerHTML = this._buildSVG();
    this._svg = this._wrapper.querySelector("svg");
    this._mouthPath = this._svg.getElementById("eva-mouth");
    this._leftEye = this._svg.getElementById("eva-left-eye");
    this._rightEye = this._svg.getElementById("eva-right-eye");

    this._startBlink();
    this._startBreathe();
    this._initParticles();
    this._particleLoop();
    this._setIdle();
  },

  // ═══════════════════════════════════════════════════════════
  // SVG CHARACTER — stylized female figure, accent-themed
  // ═══════════════════════════════════════════════════════════
  _buildSVG() {
    return `
<svg viewBox="0 0 400 600" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="bgGlow" cx="50%" cy="18%" r="40%">
      <stop offset="0%" stop-color="var(--accent-light)" stop-opacity="0.5"/>
      <stop offset="100%" stop-color="var(--accent-light)" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="hairGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--text)"/>
      <stop offset="100%" stop-color="var(--text)" stop-opacity="0.8"/>
    </linearGradient>
    <linearGradient id="dressGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--accent)"/>
      <stop offset="100%" stop-color="var(--accent)" stop-opacity="0.6"/>
    </linearGradient>
  </defs>

  <!-- Background glow -->
  <circle cx="200" cy="100" r="180" fill="url(#bgGlow)"/>

  <!-- Hair back layer -->
  <path d="M130 90 Q120 200 115 280 Q140 270 160 290 Q150 200 155 90 Z" fill="url(#hairGrad)" opacity="0.7"/>
  <path d="M270 90 Q280 200 285 280 Q260 270 240 290 Q250 200 245 90 Z" fill="url(#hairGrad)" opacity="0.7"/>

  <!-- Body / Torso -->
  <path d="M165 230 Q160 260 155 380 Q160 390 200 395 Q240 390 245 380 Q240 260 235 230 Z"
        fill="var(--accent)" opacity="0.15" stroke="var(--accent)" stroke-width="1" stroke-opacity="0.3"/>

  <!-- Dress — flowing layered paths -->
  <path d="M155 320 Q145 380 130 430 L135 440 Q170 420 200 430 Q230 420 265 440 L270 430 Q255 380 245 320 Z"
        fill="url(#dressGrad)" opacity="0.8"/>
  <path d="M148 340 Q140 400 125 445 L200 455 L275 445 Q260 400 252 340 Z"
        fill="var(--accent)" opacity="0.15"/>

  <!-- Arms -->
  <path d="M165 240 Q140 290 130 350 Q128 370 135 375 Q140 370 140 350 Q148 290 170 250"
        fill="none" stroke="var(--text)" stroke-width="2.5" stroke-linecap="round" opacity="0.3"/>
  <path d="M235 240 Q260 290 270 350 Q272 370 265 375 Q260 370 260 350 Q252 290 230 250"
        fill="none" stroke="var(--text)" stroke-width="2.5" stroke-linecap="round" opacity="0.3"/>

  <!-- Neck -->
  <rect x="187" y="195" width="26" height="22" rx="3" fill="var(--text)" opacity="0.12"/>

  <!-- Head -->
  <ellipse cx="200" cy="130" rx="55" ry="65" fill="var(--text)" opacity="0.1" stroke="var(--border)" stroke-width="1"/>

  <!-- Hair front — bangs -->
  <path d="M145 110 Q150 55 165 40 Q175 33 185 35 L185 42 Q180 38 170 42 Q160 50 155 80 Q150 95 147 110 Z"
        fill="url(#hairGrad)"/>
  <path d="M255 110 Q250 55 235 40 Q225 33 215 35 L215 42 Q220 38 230 42 Q240 50 245 80 Q250 95 253 110 Z"
        fill="url(#hairGrad)"/>
  <path d="M155 70 Q175 30 200 28 Q225 30 245 70 Q235 45 200 42 Q165 45 155 70 Z"
        fill="url(#hairGrad)"/>

  <!-- Side hair strands -->
  <path d="M145 110 Q138 160 140 200 Q142 160 148 110" fill="url(#hairGrad)"/>
  <path d="M255 110 Q262 160 260 200 Q258 160 252 110" fill="url(#hairGrad)"/>

  <!-- Eyebrows -->
  <path d="M172 98 Q180 93 192 96" fill="none" stroke="var(--text)" stroke-width="2" stroke-linecap="round" opacity="0.5"/>
  <path d="M228 98 Q220 93 208 96" fill="none" stroke="var(--text)" stroke-width="2" stroke-linecap="round" opacity="0.5"/>

  <!-- Left Eye -->
  <g id="eva-left-eye" transform="translate(177, 110)">
    <ellipse cx="0" cy="0" rx="14" ry="9" fill="white" opacity="0.9"/>
    <ellipse cx="1" cy="0" rx="8" ry="8" fill="var(--accent)"/>
    <ellipse cx="1" cy="0" rx="4" ry="4" fill="var(--header)"/>
    <ellipse cx="4" cy="-2" rx="2" ry="1.5" fill="white" opacity="0.8"/>
    <!-- Eyelid -->
    <rect x="-15" y="-12" width="30" height="12" rx="6" fill="var(--text)" opacity="0.08"
          style="transform-origin:center;animation:avatar-blink-keyframes 6s infinite;"/>
  </g>

  <!-- Right Eye -->
  <g id="eva-right-eye" transform="translate(223, 110)">
    <ellipse cx="0" cy="0" rx="14" ry="9" fill="white" opacity="0.9"/>
    <ellipse cx="-1" cy="0" rx="8" ry="8" fill="var(--accent)"/>
    <ellipse cx="-1" cy="0" rx="4" ry="4" fill="var(--header)"/>
    <ellipse cx="2" cy="-2" rx="2" ry="1.5" fill="white" opacity="0.8"/>
    <!-- Eyelid -->
    <rect x="-15" y="-12" width="30" height="12" rx="6" fill="var(--text)" opacity="0.08"
          style="transform-origin:center;animation:avatar-blink-keyframes 6s infinite;"/>
  </g>

  <!-- Nose -->
  <path d="M200 115 Q197 128 198 132 Q200 134 202 132 Q203 128 200 115"
        fill="none" stroke="var(--text)" stroke-width="1" opacity="0.25"/>

  <!-- Mouth -->
  <path id="eva-mouth"
        d="M190 148 Q195 152 200 152 Q205 152 210 148"
        fill="none" stroke="var(--text)" stroke-width="2" stroke-linecap="round" opacity="0.5"/>

  <!-- Earpiece / Tech accessory -->
  <circle cx="145" cy="125" r="5" fill="none" stroke="var(--accent)" stroke-width="2" opacity="0.6"/>
  <circle cx="145" cy="125" r="2" fill="var(--accent)" opacity="0.6"/>
  <path d="M145 130 Q148 140 155 145" fill="none" stroke="var(--accent)" stroke-width="1.5" opacity="0.4"/>

  <!-- Collarbone detail -->
  <path d="M180 200 Q190 208 200 212 Q210 208 220 200"
        fill="none" stroke="var(--text)" stroke-width="1" opacity="0.12"/>

  <!-- Subtle chest glow (energy core) -->
  <circle cx="200" cy="235" r="8" fill="var(--accent-light)" opacity="0.4">
    <animate attributeName="r" values="7;10;7" dur="3s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="0.3;0.5;0.3" dur="3s" repeatCount="indefinite"/>
  </circle>
</svg>`;
  },

  // ═══════════════════════════════════════════════════════════
  // STATE MACHINE
  // ═══════════════════════════════════════════════════════════

  _setIdle() {
    this._state = "idle";
    if (!this._wrapper) return;
    this._wrapper.style.transition = "transform var(--transition-zoom)";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.5) translateY(0)";
    this._wrapper.classList.remove("listening");
    this._svg && this._svg.classList.remove("responding");
    this._stopMouth();
    this._setParticles("calm");
    this._updateLabel("avatar.idle");
  },

  setListening() {
    if (this._state === "responding") return;
    this._state = "listening";
    if (!this._wrapper) return;
    this._wrapper.style.transition = "transform 400ms cubic-bezier(0.4, 0, 0.2, 1)";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.8) translateY(2px)";
    this._wrapper.classList.add("listening");
    this._updateLabel("avatar.listening");
  },

  setResponding() {
    this._state = "responding";
    if (!this._wrapper) return;
    // Snap origin to body center, then animate zoom out
    this._wrapper.style.transition = "none";
    this._wrapper.style.transformOrigin = "50% 45%";
    this._wrapper.offsetHeight; // force reflow
    this._wrapper.style.transition = "transform var(--transition-zoom)";
    this._wrapper.style.transform = "scale(1.0) translateY(0)";
    this._wrapper.classList.remove("listening");
    this._svg && this._svg.classList.add("responding");
    this._startMouth();
    this._setParticles("active");
    this._updateLabel("avatar.responding");
  },

  mouthCycle() {
    if (!this._mouthPath) return;
    const open = this._mouthPath.dataset.open === "1";
    if (open) {
      this._mouthPath.setAttribute("d", "M190 148 Q195 152 200 152 Q205 152 210 148");
      this._mouthPath.setAttribute("opacity", "0.5");
    } else {
      this._mouthPath.setAttribute("d", "M188 144 Q194 154 200 156 Q206 154 212 144");
      this._mouthPath.setAttribute("opacity", "0.6");
      this._mouthPath.setAttribute("stroke-width", "3");
    }
    this._mouthPath.dataset.open = open ? "0" : "1";
    if (!open) {
      setTimeout(() => {
        if (this._mouthPath) this._mouthPath.setAttribute("stroke-width", "2");
      }, 120);
    }
  },

  _startMouth() {
    this._stopMouth();
    this._mouthTimer = setInterval(() => this.mouthCycle(), 280);
  },

  _stopMouth() {
    if (this._mouthTimer) { clearInterval(this._mouthTimer); this._mouthTimer = null; }
    if (this._mouthPath) {
      this._mouthPath.setAttribute("d", "M190 148 Q195 152 200 152 Q205 152 210 148");
      this._mouthPath.setAttribute("stroke-width", "2");
      this._mouthPath.dataset.open = "0";
    }
  },

  _startBlink() {
    // Blink is handled by CSS keyframes on the eyelid rects
  },

  _startBreathe() {
    // Breathing is added as inline style on the SVG
    if (this._svg) {
      this._svg.style.animation = "avatar-breathe 4s ease-in-out infinite";
    }
  },

  _updateLabel(key) {
    const el = document.getElementById("avatarStateLabel");
    if (el && I18N) { el.textContent = I18N.t(key); }
  },

  // ═══════════════════════════════════════════════════════════
  // PARTICLE SYSTEM (Canvas)
  // ═══════════════════════════════════════════════════════════

  _initParticles() {
    const c = this._particleCanvas;
    if (!c) return;
    const rect = c.parentElement.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
    this._spawnParticles(10);
  },

  _spawnParticles(count) {
    const c = this._particleCanvas;
    if (!c) return;
    for (let i = 0; i < count; i++) {
      this._particles.push({
        x: Math.random() * c.width,
        y: c.height * 0.3 + Math.random() * c.height * 0.5,
        r: 1 + Math.random() * 2,
        speed: 0.3 + Math.random() * 0.8,
        drift: (Math.random() - 0.5) * 0.4,
        opacity: Math.random() * 0.5,
      });
    }
  },

  _setParticles(mode) {
    const target = mode === "active" ? 40 : 10;
    const diff = target - this._particles.length;
    if (diff > 0) this._spawnParticles(diff);
    if (diff < 0) this._particles.length = target;
    // Set speed multiplier
    this._particles.forEach(p => {
      p.speed = mode === "active" ? 0.6 + Math.random() * 1.5 : 0.3 + Math.random() * 0.8;
    });
  },

  _particleLoop() {
    const c = this._particleCanvas;
    if (!c) { this._particleRAF = requestAnimationFrame(() => this._particleLoop()); return; }
    const ctx = c.getContext("2d");
    const w = c.width, h = c.height;

    ctx.clearRect(0, 0, w, h);

    const style = getComputedStyle(document.documentElement);
    const accentColor = style.getPropertyValue("--accent").trim() || "#0f766e";

    for (const p of this._particles) {
      p.y -= p.speed;
      p.x += p.drift;
      p.opacity -= 0.002;
      if (p.y < 0 || p.opacity <= 0) {
        p.y = h * 0.3 + Math.random() * h * 0.5;
        p.x = Math.random() * w;
        p.opacity = 0.3 + Math.random() * 0.4;
      }

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = accentColor;
      ctx.globalAlpha = p.opacity;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    this._particleRAF = requestAnimationFrame(() => this._particleLoop());
  },

  destroy() {
    if (this._particleRAF) cancelAnimationFrame(this._particleRAF);
    if (this._mouthTimer) clearInterval(this._mouthTimer);
  },
};
