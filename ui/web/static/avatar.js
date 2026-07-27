// EVA Avatar — ice-blue twin-tails techwear character + zoom state machine + particle canvas
// Animation system: unified JS RAF loop (no CSS keyframe transform conflicts)
const AvatarController = {
  _state: "idle",
  _wrapper: null,
  _svg: null,
  _mouthPath: null,
  _leftEye: null,
  _rightEye: null,
  _leftIris: null,
  _rightIris: null,
  _particleCanvas: null,
  _particles: [],
  _animRAF: null,
  _mouthTimer: null,
  _mouthShapeIdx: 0,
  _mouseX: 0, _mouseY: 0,
  _targetMouseX: 0, _targetMouseY: 0,
  _entranceDone: false,
  _idlePhase: 0,

  init(containerId, canvasId) {
    this._wrapper = document.getElementById(containerId);
    this._particleCanvas = document.getElementById(canvasId);
    if (!this._wrapper) return;

    this._wrapper.innerHTML = this._buildSVG();
    this._svg = this._wrapper.querySelector("svg");
    this._mouthPath = this._svg.getElementById("eva-mouth");
    this._leftEye = this._svg.getElementById("eva-left-eye");
    this._rightEye = this._svg.getElementById("eva-right-eye");
    this._leftIris = this._svg.getElementById("eva-left-iris");
    this._rightIris = this._svg.getElementById("eva-right-iris");

    this._bindMouseTracking();
    this._initParticles();
    this._entranceAnimation();
  },

  // ═══════════════════════════════════════════════════════════
  // ENTRANCE — fade in + gentle scale-up
  // ═══════════════════════════════════════════════════════════
  _entranceAnimation() {
    this._wrapper.style.transition = "none";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(1.8)";
    this._wrapper.style.opacity = "0";
    this._svg.style.opacity = "0";

    // Stagger: wrapper opacity → svg scale → settle
    requestAnimationFrame(() => {
      this._wrapper.style.transition = "opacity 500ms ease-out";
      this._wrapper.style.opacity = "1";
    });

    setTimeout(() => {
      this._wrapper.style.transition = "transform 800ms cubic-bezier(0.16, 1, 0.3, 1)";
      this._wrapper.style.transform = "scale(2.5) translateY(0)";
      this._svg.style.transition = "opacity 400ms ease-out";
      this._svg.style.opacity = "1";
    }, 200);

    setTimeout(() => {
      this._wrapper.style.transition = "none";
      this._entranceDone = true;
      this._setIdle();
    }, 1000);
  },

  // ═══════════════════════════════════════════════════════════
  // MOUSE TRACKING — for eye following
  // ═══════════════════════════════════════════════════════════
  _bindMouseTracking() {
    const panel = document.getElementById("avatarPanel");
    const target = panel || document;
    target.addEventListener("mousemove", (e) => {
      const rect = this._wrapper.getBoundingClientRect();
      const originX = rect.left + rect.width / 2;
      const originY = rect.top + rect.height * 0.15;
      this._targetMouseX = (e.clientX - originX) / (rect.width * 0.4);
      this._targetMouseY = (e.clientY - originY) / (rect.height * 0.4);
      this._targetMouseX = Math.max(-1, Math.min(1, this._targetMouseX));
      this._targetMouseY = Math.max(-1, Math.min(1, this._targetMouseY));
    });
    target.addEventListener("mouseleave", () => {
      this._targetMouseX = 0;
      this._targetMouseY = 0;
    });
  },

  // ═══════════════════════════════════════════════════════════
  // UNIFIED ANIMATION LOOP — all per-frame transform updates
  // ═══════════════════════════════════════════════════════════
  _startAnimLoop() {
    if (this._animRAF) return;
    const loop = (ts) => {
      this._animStep(ts);
      this._particleStep();
      this._eyeStep();
      this._animRAF = requestAnimationFrame(loop);
    };
    this._animRAF = requestAnimationFrame(loop);
  },

  _animStep(ts) {
    if (!this._entranceDone) return;
    const t = ts / 1000;

    if (this._state === "idle") {
      const breathe = Math.sin(t * 1.57) * -3; // 4s period
      // Subtle float: breathe + micro-sway
      const microSway = Math.sin(t * 0.9) * 0.15;
      this._wrapper.style.transform = `scale(2.5) translateY(${breathe}px) rotate(${microSway}deg)`;
    } else if (this._state === "listening") {
      // Gentle head tilt oscillation
      const tilt = Math.sin(t * 2.2) * 2.5;
      const microBreath = Math.sin(t * 1.9) * -1;
      this._wrapper.style.transform = `scale(2.8) translateY(${2 + microBreath}px) rotate(${tilt}deg)`;
    } else if (this._state === "responding") {
      // Full-body sway
      const sway = Math.sin(t * 1.8) * 0.6;
      const bounce = Math.abs(Math.sin(t * 2.4)) * 1.5;
      this._wrapper.style.transform = `scale(1.0) translateY(${bounce}px) rotate(${sway}deg)`;
    }
  },

  // ═══════════════════════════════════════════════════════════
  // STATE MACHINE
  // ═══════════════════════════════════════════════════════════

  _setIdle() {
    this._state = "idle";
    if (!this._wrapper || !this._entranceDone) return;
    this._wrapper.style.transition = "transform 500ms cubic-bezier(0.4, 0, 0.2, 1), filter 500ms ease";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.5) translateY(0)";
    this._wrapper.style.filter = "";
    this._wrapper.classList.remove("listening", "responding");
    this._wrapper.classList.add("idle");
    this._svg && this._svg.classList.remove("responding");
    this._stopMouth();
    this._setParticles("calm");
    this._updateLabel("avatar.idle");

    // After transition ends, hand over to RAF loop
    clearTimeout(this._transitionTimer);
    this._transitionTimer = setTimeout(() => {
      this._wrapper.style.transition = "none";
    }, 520);
    this._startAnimLoop();
  },

  setListening() {
    if (this._state === "responding") return;
    this._state = "listening";
    if (!this._wrapper || !this._entranceDone) return;
    this._wrapper.style.transition = "transform 400ms cubic-bezier(0.4, 0, 0.2, 1), filter 400ms ease";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.8) translateY(2px)";
    this._wrapper.style.filter =
      "drop-shadow(0 0 8px var(--accent)) drop-shadow(0 0 20px color-mix(in srgb, var(--accent) 30%, transparent))";
    this._wrapper.classList.remove("idle", "responding");
    this._wrapper.classList.add("listening");
    this._updateLabel("avatar.listening");

    clearTimeout(this._transitionTimer);
    this._transitionTimer = setTimeout(() => {
      this._wrapper.style.transition = "none";
    }, 420);
    this._startAnimLoop();
  },

  setResponding() {
    this._state = "responding";
    if (!this._wrapper || !this._entranceDone) return;
    // Snap origin, then animate zoom out
    this._wrapper.style.transition = "none";
    this._wrapper.style.transformOrigin = "50% 45%";
    this._wrapper.offsetHeight; // force reflow
    this._wrapper.style.transition = "transform 600ms cubic-bezier(0.4, 0, 0.2, 1), filter 400ms ease";
    this._wrapper.style.transform = "scale(1.0) translateY(0)";
    this._wrapper.style.filter = "";
    this._wrapper.classList.remove("listening", "idle");
    this._wrapper.classList.add("responding");
    this._svg && this._svg.classList.add("responding");
    this._startMouth();
    this._setParticles("active");
    this._burstParticles(10);
    this._updateLabel("avatar.responding");

    clearTimeout(this._transitionTimer);
    this._transitionTimer = setTimeout(() => {
      this._wrapper.style.transition = "none";
    }, 620);
    this._startAnimLoop();
  },

  // ═══════════════════════════════════════════════════════════
  // MOUTH — 4-frame cycle for natural speech look
  // ═══════════════════════════════════════════════════════════
  _MOUTH_SHAPES: [
    { d: "M209 156 Q214 154 220 154 Q226 154 231 156", opacity: "0.55" },       // closed
    { d: "M207 153 Q214 158 220 160 Q226 158 233 153", opacity: "0.6" },        // half
    { d: "M205 150 Q214 163 220 165 Q226 163 235 150", opacity: "0.65" },       // open
    { d: "M203 148 Q214 164 220 168 Q226 164 237 148", opacity: "0.7" },        // wide
  ],

  mouthCycle() {
    if (!this._mouthPath) return;
    this._mouthShapeIdx = (this._mouthShapeIdx + 1) % this._MOUTH_SHAPES.length;
    const shape = this._MOUTH_SHAPES[this._mouthShapeIdx];
    this._mouthPath.setAttribute("d", shape.d);
    this._mouthPath.setAttribute("opacity", shape.opacity);
  },

  _startMouth() {
    this._stopMouth();
    this._mouthShapeIdx = 0;
    this._mouthTimer = setInterval(() => this.mouthCycle(), 240);
  },

  _stopMouth() {
    if (this._mouthTimer) { clearInterval(this._mouthTimer); this._mouthTimer = null; }
    if (this._mouthPath) {
      this._mouthPath.setAttribute("d", "M209 156 Q214 154 220 154 Q226 154 231 156");
      this._mouthPath.setAttribute("opacity", "0.55");
      this._mouthShapeIdx = 0;
    }
  },

  _updateLabel(key) {
    const el = document.getElementById("avatarStateLabel");
    if (el && I18N) { el.textContent = I18N.t(key); }
  },

  // ═══════════════════════════════════════════════════════════
  // EYE TRACKING — smooth follow of mouse position
  // ═══════════════════════════════════════════════════════════
  _eyeStep() {
    if (!this._leftIris || !this._rightIris) return;

    if (this._state !== "idle" && this._state !== "listening") {
      this._leftIris.setAttribute("transform", "translate(0,0)");
      this._rightIris.setAttribute("transform", "translate(0,0)");
      return;
    }

    // Smooth lerp toward target
    this._mouseX += (this._targetMouseX - this._mouseX) * 0.08;
    this._mouseY += (this._targetMouseY - this._mouseY) * 0.08;

    const clamp = (v) => Math.max(-2.5, Math.min(2.5, v));
    const dx = clamp(this._mouseX * 3);
    const dy = clamp(this._mouseY * 2.5);

    this._leftIris.setAttribute("transform", `translate(${dx},${dy})`);
    this._rightIris.setAttribute("transform", `translate(${dx},${dy})`);
  },

  // ═══════════════════════════════════════════════════════════
  // PARTICLE SYSTEM — ambient energy motes
  // ═══════════════════════════════════════════════════════════

  _initParticles() {
    const c = this._particleCanvas;
    if (!c) return;
    this._resizeCanvas();
    this._spawnParticles(12);
    window.addEventListener("resize", () => this._resizeCanvas());
  },

  _resizeCanvas() {
    const c = this._particleCanvas;
    if (!c || !c.parentElement) return;
    const rect = c.parentElement.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
  },

  _spawnParticles(count) {
    const c = this._particleCanvas;
    if (!c) return;
    for (let i = 0; i < count; i++) {
      const hueRoll = Math.random();
      const hue = hueRoll < 0.5 ? "accent" : hueRoll < 0.75 ? "teal" : "white";
      this._particles.push({
        x: Math.random() * c.width,
        y: c.height * 0.2 + Math.random() * c.height * 0.6,
        r: 0.6 + Math.random() * 2.5,
        speed: 0.2 + Math.random() * 1.0,
        drift: (Math.random() - 0.5) * 0.6,
        opacity: 0.15 + Math.random() * 0.45,
        fadeRate: 0.0008 + Math.random() * 0.002,
        hue,
        shape: Math.random() < 0.25 ? "diamond" : "circle",
        glow: Math.random() < 0.3,
        life: Math.random(),
      });
    }
  },

  _burstParticles(count) {
    const c = this._particleCanvas;
    if (!c) return;
    const cx = c.width / 2;
    const cy = c.height * 0.45;
    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = 40 + Math.random() * 100;
      this._particles.push({
        x: cx + Math.cos(angle) * 10,
        y: cy + Math.sin(angle) * 10,
        r: 1.5 + Math.random() * 3.5,
        speed: -1.5 - Math.random() * 2.5,
        drift: Math.cos(angle) * 2,
        opacity: 0.7 + Math.random() * 0.3,
        fadeRate: 0.008 + Math.random() * 0.015,
        hue: Math.random() < 0.5 ? "teal" : "accent",
        shape: "circle",
        glow: true,
        life: 1,
        burst: true,
      });
    }
  },

  _setParticles(mode) {
    const target = mode === "active" ? 45 : 12;
    const diff = target - this._particles.length;
    if (diff > 0) this._spawnParticles(diff);
    if (diff < 0) this._particles.length = target;
    this._particles.forEach(p => {
      if (!p.burst) {
        p.speed = mode === "active" ? 0.4 + Math.random() * 1.8 : 0.2 + Math.random() * 1.0;
      }
    });
  },

  _particleStep() {
    const c = this._particleCanvas;
    if (!c) return;
    const ctx = c.getContext("2d");
    const w = c.width, h = c.height;

    ctx.clearRect(0, 0, w, h);

    const style = getComputedStyle(document.documentElement);
    const accentColor = style.getPropertyValue("--accent").trim() || "#0f766e";
    const accentLight = style.getPropertyValue("--accent-light").trim() || "#ccfbf1";

    const len = this._particles.length;
    for (let i = len - 1; i >= 0; i--) {
      const p = this._particles[i];
      p.y -= p.speed;
      p.x += p.drift;
      p.opacity -= p.fadeRate;
      p.life -= p.fadeRate;

      if (p.y < -20 || p.opacity <= 0 || (p.burst && p.life <= 0)) {
        this._particles.splice(i, 1);
        continue;
      }

      let color;
      if (p.hue === "accent") color = accentColor;
      else if (p.hue === "teal") color = "#2dd4bf";
      else color = accentLight;

      ctx.save();
      ctx.globalAlpha = Math.max(0, p.opacity);

      if (p.glow) {
        ctx.shadowColor = color;
        ctx.shadowBlur = p.burst ? 10 : 5;
      }

      if (p.shape === "diamond") {
        ctx.beginPath();
        ctx.moveTo(p.x, p.y - p.r);
        ctx.lineTo(p.x + p.r * 0.7, p.y);
        ctx.lineTo(p.x, p.y + p.r);
        ctx.lineTo(p.x - p.r * 0.7, p.y);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }

      ctx.restore();
    }

    // Replenish ambient particles if below target
    const target = this._state === "responding" ? 45 : 12;
    const nonBurst = this._particles.filter(p => !p.burst).length;
    if (nonBurst < target && Math.random() < 0.3) {
      this._spawnParticles(1);
    }
  },

  destroy() {
    if (this._animRAF) cancelAnimationFrame(this._animRAF);
    if (this._mouthTimer) clearInterval(this._mouthTimer);
    this._animRAF = null;
  },

  // ═══════════════════════════════════════════════════════════
  // SVG — ice-blue twin-tails, gray-blue kuudere eyes, techwear outfit
  // ═══════════════════════════════════════════════════════════
  _buildSVG() {
    return `
<svg viewBox="0 0 440 680" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="bgGlow" cx="50%" cy="12%" r="40%">
      <stop offset="0%" stop-color="#80d8e8" stop-opacity="0.1"/>
      <stop offset="100%" stop-color="transparent" stop-opacity="0"/>
    </radialGradient>

    <!-- Twin-tail gradient: silver-white root → ice blue → aqua-cyan tip -->
    <linearGradient id="tailGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f4f8fc"/>
      <stop offset="15%" stop-color="#e8f0f8"/>
      <stop offset="40%" stop-color="#c8ddf0"/>
      <stop offset="65%" stop-color="#98c8e0"/>
      <stop offset="85%" stop-color="#68b0d0"/>
      <stop offset="100%" stop-color="#48a0c0"/>
    </linearGradient>
    <linearGradient id="tailGradInner" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f8fcff"/>
      <stop offset="20%" stop-color="#e0eef8"/>
      <stop offset="50%" stop-color="#b8d8ec"/>
      <stop offset="80%" stop-color="#80bcd8"/>
      <stop offset="100%" stop-color="#58a8c4"/>
    </linearGradient>
    <linearGradient id="tailTip" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#88c0d8"/>
      <stop offset="100%" stop-color="#48a8c8"/>
    </linearGradient>

    <!-- Skin -->
    <linearGradient id="skinBase" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#fefaf6"/>
      <stop offset="60%" stop-color="#fdf4ec"/>
      <stop offset="100%" stop-color="#f5e6d8"/>
    </linearGradient>
    <radialGradient id="skinBlush" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#f0c8c0" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="#fdf2e8" stop-opacity="0"/>
    </radialGradient>

    <!-- Gray-blue eye -->
    <radialGradient id="eyeBlue" cx="50%" cy="35%" r="55%">
      <stop offset="0%" stop-color="#8898b8"/>
      <stop offset="40%" stop-color="#6a7ea8"/>
      <stop offset="80%" stop-color="#4a5c80"/>
      <stop offset="100%" stop-color="#2a3858"/>
    </radialGradient>

    <!-- Jacket: light gray techwear -->
    <linearGradient id="jacketGray" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#eaecf0"/>
      <stop offset="40%" stop-color="#e0e3e8"/>
      <stop offset="100%" stop-color="#d2d6dc"/>
    </linearGradient>
    <linearGradient id="jacketShadow" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#d8dce2"/>
      <stop offset="100%" stop-color="#c4c9d0"/>
    </linearGradient>

    <!-- Dark navy (collar + skirt) -->
    <linearGradient id="navyDark" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1e3458"/>
      <stop offset="50%" stop-color="#182c4c"/>
      <stop offset="100%" stop-color="#122040"/>
    </linearGradient>

    <!-- Hair ornament: black with purple-pink edge -->
    <linearGradient id="crossStroke" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#c878a0"/>
      <stop offset="50%" stop-color="#d090b0"/>
      <stop offset="100%" stop-color="#b86898"/>
    </linearGradient>
  </defs>

  <!-- ═══ BG GLOW ═══ -->
  <circle cx="220" cy="100" r="210" fill="url(#bgGlow)"/>

  <!-- ═══════════════════════════════════════════════════
       TWIN TAILS — BACK LAYER (behind body)
       Super-long, high position, ice-blue gradient
       ═══════════════════════════════════════════════════ -->
  <!-- LEFT twin tail — back/outer mass -->
  <path d="M175 85 Q148 100 130 150 Q110 210 98 280 Q86 350 84 420 Q82 480 88 530 Q94 565 106 575 Q112 578 118 570 Q114 540 112 490 Q110 430 114 370 Q118 300 130 230 Q142 160 160 110 Z"
        fill="url(#tailGrad)" opacity="0.55"/>
  <path d="M172 82 Q140 105 118 165 Q98 230 88 310 Q80 380 82 450 Q84 510 92 550 Q100 575 110 575 Q106 545 104 500 Q102 440 106 375 Q112 300 124 225 Q138 150 158 100 Z"
        fill="url(#tailGradInner)" opacity="0.4"/>
  <path d="M165 88 Q130 130 112 200 Q96 270 88 350 Q82 420 86 480 Q90 530 100 560 Q106 572 112 570 Q108 535 106 480 Q104 415 108 340 Q114 260 128 185 Q142 120 160 95 Z"
        fill="url(#tailTip)" opacity="0.35"/>

  <!-- RIGHT twin tail — back/outer mass -->
  <path d="M265 85 Q292 100 310 150 Q330 210 342 280 Q354 350 356 420 Q358 480 352 530 Q346 565 334 575 Q328 578 322 570 Q326 540 328 490 Q330 430 326 370 Q322 300 310 230 Q298 160 280 110 Z"
        fill="url(#tailGrad)" opacity="0.55"/>
  <path d="M268 82 Q300 105 322 165 Q342 230 352 310 Q360 380 358 450 Q356 510 348 550 Q340 575 330 575 Q334 545 336 500 Q338 440 334 375 Q328 300 316 225 Q302 150 282 100 Z"
        fill="url(#tailGradInner)" opacity="0.4"/>
  <path d="M275 88 Q310 130 328 200 Q344 270 352 350 Q358 420 354 480 Q350 530 340 560 Q334 572 328 570 Q332 535 334 480 Q336 415 332 340 Q326 260 312 185 Q298 120 280 95 Z"
        fill="url(#tailTip)" opacity="0.35"/>

  <!-- ═══════════════════════════════════════════════════
       LEGS + SHOES
       ═══════════════════════════════════════════════════ -->
  <!-- Left leg -->
  <path d="M185 445 Q180 480 178 520 Q176 555 178 585 Q180 600 188 608 L192 608 Q192 590 190 560 Q188 520 190 480 Z"
        fill="#f8f2ec"/>
  <!-- Right leg -->
  <path d="M255 445 Q260 480 262 520 Q264 555 262 585 Q260 600 252 608 L248 608 Q248 590 250 560 Q252 520 250 480 Z"
        fill="#f8f2ec"/>

  <!-- Left shoe — future sneaker -->
  <path d="M172 600 Q170 615 176 630 Q182 642 196 645 Q210 644 214 632 Q214 618 208 605 Q200 600 188 600 Z"
        fill="#f0f2f4" stroke="#c0c4c8" stroke-width="0.8"/>
  <path d="M176 618 Q180 636 194 640 Q204 638 206 628"
        fill="none" stroke="#40b8c8" stroke-width="1.5" opacity="0.6"/>
  <path d="M188 625 L192 630 Q196 628 196 622"
        fill="none" stroke="#d090b0" stroke-width="1" opacity="0.4"/>
  <!-- Sole -->
  <path d="M174 642 Q186 648 200 646 Q210 644 214 638 L214 642 Q210 648 198 650 Q184 650 174 642 Z"
        fill="#222"/>

  <!-- Right shoe — future sneaker -->
  <path d="M268 600 Q270 615 264 630 Q258 642 244 645 Q230 644 226 632 Q226 618 232 605 Q240 600 252 600 Z"
        fill="#f0f2f4" stroke="#c0c4c8" stroke-width="0.8"/>
  <path d="M264 618 Q260 636 246 640 Q236 638 234 628"
        fill="none" stroke="#40b8c8" stroke-width="1.5" opacity="0.6"/>
  <path d="M252 625 L248 630 Q244 628 244 622"
        fill="none" stroke="#d090b0" stroke-width="1" opacity="0.4"/>
  <path d="M266 642 Q254 648 240 646 Q230 644 226 638 L226 642 Q230 648 242 650 Q256 650 266 642 Z"
        fill="#222"/>

  <!-- ═══════════════════════════════════════════════════
       SKIRT — dark blue pleated, cyan hem line
       ═══════════════════════════════════════════════════ -->
  <path d="M172 395 Q165 410 164 435 Q163 455 168 470 L180 468 Q178 450 178 435 Q178 410 182 395 Z"
        fill="url(#navyDark)"/>
  <path d="M185 393 Q182 410 180 435 Q178 455 180 470 L194 468 Q194 450 195 435 Q196 410 198 393 Z"
        fill="#1a3050"/>
  <path d="M200 392 Q200 410 200 435 Q200 455 202 470 L214 468 Q214 450 213 435 Q212 410 212 392 Z"
        fill="url(#navyDark)"/>
  <path d="M215 392 Q216 410 216 435 Q216 455 215 470 L228 468 Q228 450 229 435 Q230 410 229 392 Z"
        fill="#1a3050"/>
  <path d="M230 393 Q232 410 232 435 Q232 455 230 470 L242 468 Q242 450 241 435 Q240 410 238 393 Z"
        fill="url(#navyDark)"/>
  <path d="M243 393 Q244 410 243 435 Q242 455 241 470 L254 468 Q254 450 254 435 Q254 410 253 393 Z"
        fill="#1a3050"/>
  <path d="M254 395 Q257 410 257 435 Q257 455 255 470 L268 470 Q268 455 268 435 Q268 410 262 395 Z"
        fill="url(#navyDark)"/>

  <!-- Skirt waistband -->
  <path d="M170 392 L270 392 L268 400 Q220 406 172 400 Z"
        fill="#162848"/>
  <!-- Cyan hem line -->
  <path d="M167 465 Q195 478 220 480 Q245 478 273 465"
        fill="none" stroke="#50c0c8" stroke-width="1.8" opacity="0.6"/>
  <path d="M166 468 Q195 481 220 483 Q245 481 274 468"
        fill="none" stroke="#40b0b8" stroke-width="1" opacity="0.35"/>

  <!-- ═══════════════════════════════════════════════════
       TORSO / JACKET BASE
       Oversized light gray techwear
       ═══════════════════════════════════════════════════ -->
  <!-- Jacket main body — oversized silhouette -->
  <path d="M155 210 Q148 240 148 290 Q148 340 155 385 Q160 405 172 398 Q185 392 188 400 L200 398 L212 400 L240 400 L252 398 Q255 392 268 400 Q280 405 285 385 Q292 340 292 290 Q292 240 285 210 Z"
        fill="url(#jacketGray)"/>

  <!-- Jacket shadow / depth sides -->
  <path d="M155 210 Q150 250 150 300 Q150 350 156 385 Q162 400 172 398 Q162 380 160 340 Q158 290 160 240 Q163 220 168 210 Z"
        fill="url(#jacketShadow)" opacity="0.5"/>
  <path d="M285 210 Q290 250 290 300 Q290 350 284 385 Q278 400 268 398 Q278 380 280 340 Q282 290 280 240 Q277 220 272 210 Z"
        fill="url(#jacketShadow)" opacity="0.5"/>

  <!-- Jacket hem band -->
  <path d="M153 388 Q172 402 190 400 Q210 396 220 398 Q230 400 250 398 Q268 400 287 388 Q280 395 260 400 Q240 406 220 408 Q200 408 180 400 Q163 394 153 388 Z"
        fill="#c8ccd4"/>

  <!-- ═══════════════════════════════════════════════════
       ARMS (inside jacket sleeves)
       ═══════════════════════════════════════════════════ -->
  <!-- Left sleeve -->
  <path d="M158 215 Q140 250 130 300 Q122 345 122 380 Q122 400 130 405 Q140 408 148 395 Q152 370 154 330 Q155 285 160 250 L162 218 Z"
        fill="url(#jacketGray)" stroke="#c8ccd4" stroke-width="0.5"/>
  <!-- Cyan webbing stripe left -->
  <path d="M146 270 Q142 310 140 350 Q138 375 142 395"
        fill="none" stroke="#50b8c8" stroke-width="2" opacity="0.5"/>
  <path d="M144 270 Q140 310 138 350 Q136 375 140 395"
        fill="none" stroke="#70d0d8" stroke-width="0.8" opacity="0.3"/>

  <!-- Right sleeve -->
  <path d="M282 215 Q300 250 310 300 Q318 345 318 380 Q318 400 310 405 Q300 408 292 395 Q288 370 286 330 Q285 285 280 250 L278 218 Z"
        fill="url(#jacketGray)" stroke="#c8ccd4" stroke-width="0.5"/>
  <!-- Cyan webbing stripe right -->
  <path d="M294 270 Q298 310 300 350 Q302 375 298 395"
        fill="none" stroke="#50b8c8" stroke-width="2" opacity="0.5"/>
  <path d="M296 270 Q300 310 302 350 Q304 375 300 395"
        fill="none" stroke="#70d0d8" stroke-width="0.8" opacity="0.3"/>

  <!-- ═══════════════════════════════════════════════════
       JACKET FRONT DETAILS
       ═══════════════════════════════════════════════════ -->
  <!-- Center zipper line -->
  <path d="M220 212 L220 395"
        fill="none" stroke="#b0b8c0" stroke-width="1.2" opacity="0.6"/>
  <path d="M218 250 L222 250 M218 290 L222 290 M218 330 L222 330 M218 370 L222 370"
        fill="none" stroke="#a0a8b0" stroke-width="0.8" opacity="0.3"/>

  <!-- Left chest pocket (modular techwear) -->
  <path d="M170 260 L190 258 L192 285 L172 287 Z"
        fill="#dce0e6" stroke="#c0c8d0" stroke-width="0.8"/>
  <path d="M174 264 L186 262 L187 272 L175 274 Z"
        fill="#c8ced6" opacity="0.4"/>

  <!-- Right chest patch -->
  <path d="M250 258 L270 260 L268 287 L248 285 Z"
        fill="#dce0e6" stroke="#c0c8d0" stroke-width="0.8"/>
  <path d="M254 264 L266 265 L265 275 L253 273 Z"
        fill="#c8ced6" opacity="0.3"/>

  <!-- EVA badge -->
  <circle cx="220" cy="275" r="9" fill="#1a2a40" stroke="#c878a0" stroke-width="1"/>
  <text x="220" y="279" text-anchor="middle" font-size="9" font-weight="bold" fill="#d0a0b8" font-family="sans-serif">EVA</text>

  <!-- Geometric accent lines -->
  <path d="M182 310 L198 308 L200 320 L184 322 Z"
        fill="none" stroke="#50b8c8" stroke-width="1.2" opacity="0.4"/>
  <path d="M242 308 L258 310 L256 322 L240 320 Z"
        fill="none" stroke="#c878a0" stroke-width="1.2" opacity="0.4"/>

  <!-- Horizontal webbing strap -->
  <path d="M162 340 Q190 336 220 335 Q250 336 278 340"
        fill="none" stroke="#c0c8d0" stroke-width="1.5" opacity="0.4"/>
  <path d="M164 344 Q190 340 220 339 Q250 340 276 344"
        fill="none" stroke="#b0b8c0" stroke-width="0.7" opacity="0.25"/>

  <!-- Left leg ring -->
  <path d="M174 480 L186 478 L188 492 L176 494 Z"
        fill="#1a1a1a" stroke="#333" stroke-width="0.5"/>
  <path d="M176 484 L184 483 L184 490 L176 491 Z"
        fill="#222"/>

  <!-- ═══════════════════════════════════════════════════
       INNER COLLAR — dark blue high collar, covers neck
       ═══════════════════════════════════════════════════ -->
  <!-- Collar back -->
  <path d="M195 190 Q188 172 192 158 Q198 148 210 145 Q220 143 230 145 Q242 148 248 158 Q252 172 245 190 Q240 182 230 177 Q220 174 210 177 Q200 182 195 190 Z"
        fill="url(#navyDark)"/>
  <!-- Collar front (visible above jacket) -->
  <path d="M194 195 Q188 178 192 162 Q196 152 208 148 Q218 146 226 148 Q236 150 240 158 Q244 172 240 192 Q238 184 228 180 Q218 178 210 180 Q202 184 194 195 Z"
        fill="#1e3a5c"/>
  <!-- Collar front darker center -->
  <path d="M200 198 Q196 180 200 166 Q204 158 210 155 Q218 153 224 155 Q230 158 232 164 Q234 178 232 195 Q228 188 222 184 Q216 182 212 184 Q206 188 200 198 Z"
        fill="url(#navyDark)"/>
  <!-- Collar cyan trim line -->
  <path d="M193 190 Q186 170 190 154 Q196 143 208 139 Q218 137 228 140 Q238 144 243 154 Q248 172 241 190"
        fill="none" stroke="#48b8c8" stroke-width="1.2" opacity="0.5"/>
  <path d="M194 188 Q188 170 192 156 Q197 146 208 142 Q218 140 226 143 Q236 147 240 156 Q244 172 238 188"
        fill="none" stroke="#60d0d8" stroke-width="0.6" opacity="0.3"/>

  <!-- ═══════════════════════════════════════════════════
       NECK (barely visible above collar)
       ═══════════════════════════════════════════════════ -->
  <path d="M210 145 Q208 155 210 162 L230 162 Q232 155 230 145 Z"
        fill="url(#skinBase)"/>
  <path d="M212 152 Q214 155 220 156 Q226 155 228 152"
        fill="none" stroke="#e0c8b0" stroke-width="0.5" opacity="0.25"/>

  <!-- ═══════════════════════════════════════════════════
       HEAD / FACE
       Small oval face, slightly pointed chin
       ═══════════════════════════════════════════════════ -->
  <ellipse cx="220" cy="120" rx="54" ry="58" fill="#fdf4ec"/>

  <path d="M172 124 Q170 96 184 74 Q198 58 216 52 Q226 50 236 52 Q254 58 268 76 Q280 98 278 126 Q280 156 266 180 Q256 198 240 206 Q226 209 214 206 Q196 198 184 180 Q172 158 172 124 Z"
        fill="url(#skinBase)" stroke="#e8d4c4" stroke-width="0.4"/>

  <!-- Jaw shading -->
  <path d="M186 174 Q200 194 220 198 Q240 194 254 174 Q240 186 220 188 Q200 186 186 174 Z"
        fill="#edd8c8" opacity="0.25"/>
  <path d="M198 196 Q212 205 220 207 Q228 205 242 196 Q228 201 220 202 Q212 201 198 196 Z"
        fill="#e0c8b0" opacity="0.25"/>

  <!-- Cheek blush -->
  <ellipse cx="178" cy="140" rx="12" ry="8" fill="url(#skinBlush)"/>
  <ellipse cx="262" cy="140" rx="12" ry="8" fill="url(#skinBlush)"/>

  <!-- ═══════════════════════════════════════════════════
       EYEBROWS — thin, cool expression
       ═══════════════════════════════════════════════════ -->
  <path d="M182 96 Q190 92 200 93 Q210 94 216 97"
        fill="none" stroke="#6a5040" stroke-width="1.8" stroke-linecap="round" opacity="0.55"/>
  <path d="M258 96 Q250 92 240 93 Q230 94 224 97"
        fill="none" stroke="#6a5040" stroke-width="1.8" stroke-linecap="round" opacity="0.55"/>

  <!-- ═══════════════════════════════════════════════════
       HAIR CROWN
       ═══════════════════════════════════════════════════ -->
  <path d="M172 96 Q168 52 184 30 Q198 16 220 14 Q242 16 256 30 Q272 52 268 96 Q270 78 262 58 Q250 38 236 28 Q224 24 216 28 Q200 36 186 56 Q178 76 172 96 Z"
        fill="#eaf2f8"/>
  <path d="M186 48 Q200 34 220 30 Q240 34 254 48 Q244 40 232 36 Q220 34 208 36 Q196 40 186 48 Z"
        fill="#ffffff" opacity="0.4"/>

  <!-- ═══════════════════════════════════════════════════
       HAIR BANGS — thick, left-parted
       ═══════════════════════════════════════════════════ -->
  <!-- Left-swept main bang (largest, covering left side, reveals right) -->
  <path d="M170 98 Q164 58 172 34 Q180 18 194 14 Q206 12 216 14 L214 22 Q206 20 198 24 Q186 30 180 52 Q174 74 172 100 Z"
        fill="#f4f8fc"/>
  <path d="M170 100 Q166 64 174 40 Q180 26 192 20 L190 28 Q184 34 178 54 Q174 76 172 102 Z"
        fill="#ffffff" opacity="0.2"/>

  <!-- Center bangs (thick, layered) -->
  <path d="M175 102 Q180 60 192 38 Q200 24 212 20 L210 26 Q202 30 196 46 Q188 64 184 104 Z"
        fill="#e8f0f6"/>
  <path d="M186 104 Q192 64 206 42 Q214 28 224 22 L222 30 Q214 34 208 50 Q200 70 196 106 Z"
        fill="#eef4fa"/>
  <path d="M198 106 Q204 68 218 46 Q226 32 236 26 L234 34 Q226 38 220 54 Q212 74 208 108 Z"
        fill="#e4eef6"/>

  <!-- Right bangs (shorter, reveals more forehead right side) -->
  <path d="M226 106 Q234 74 246 52 Q254 38 264 32 L262 40 Q254 46 248 62 Q240 80 236 108 Z"
        fill="#eaf2f8"/>
  <path d="M244 104 Q252 76 262 58 Q270 46 276 40 L274 48 Q268 54 260 68 Q252 84 246 106 Z"
        fill="#f0f6fc"/>

  <!-- Wispy bangs overlay -->
  <path d="M180 64 Q188 46 200 36 Q208 32 216 32 L214 38 Q208 38 200 46 Q190 56 186 76 Z"
        fill="#ffffff" opacity="0.25"/>
  <path d="M240 72 Q250 56 260 48 Q266 44 272 42 L271 48 Q265 52 258 60 Q248 70 244 82 Z"
        fill="#ffffff" opacity="0.2"/>

  <!-- Forehead wisps -->
  <path d="M176 88 Q172 82 170 76 Q168 70 172 66"
        fill="none" stroke="#d8e4f0" stroke-width="1" stroke-linecap="round" opacity="0.3"/>
  <path d="M262 86 Q266 80 268 74 Q270 68 266 64"
        fill="none" stroke="#dce6f2" stroke-width="1" stroke-linecap="round" opacity="0.3"/>

  <!-- ═══════════════════════════════════════════════════
       SIDE FRAMING HAIR (long strands framing face)
       ═══════════════════════════════════════════════════ -->
  <!-- Left side -->
  <path d="M168 106 Q156 110 148 125 Q140 142 138 165 Q137 180 140 195 L143 193 Q143 176 146 158 Q150 138 158 120 Q164 112 168 108 Z"
        fill="#f0f6fc"/>
  <path d="M166 116 Q150 122 138 148 Q130 168 128 195 Q127 215 130 232 L134 230 Q133 212 135 192 Q138 168 144 148 Q152 128 164 120 Z"
        fill="#e4eef6"/>
  <path d="M164 126 Q145 135 132 165 Q122 188 120 220 Q119 245 123 262 L127 260 Q126 242 127 218 Q130 188 138 162 Q148 140 162 130 Z"
        fill="#c8ddf0" opacity="0.4"/>

  <!-- Right side -->
  <path d="M272 106 Q284 110 292 125 Q300 142 302 165 Q303 178 300 195 L297 193 Q297 176 294 158 Q290 138 282 120 Q276 112 272 108 Z"
        fill="#eef4fa"/>
  <path d="M274 116 Q290 124 302 150 Q310 170 312 200 Q313 220 310 235 L306 233 Q307 215 306 195 Q303 170 296 150 Q288 132 276 122 Z"
        fill="#e0ecf4"/>
  <path d="M276 128 Q295 138 308 168 Q318 192 320 222 Q321 248 317 264 L313 262 Q314 244 313 220 Q308 190 300 165 Q290 144 278 132 Z"
        fill="#c8ddf0" opacity="0.35"/>

  <!-- ═══════════════════════════════════════════════════
       TWIN TAILS — FRONT OVER-SHOULDER LAYERS
       ═══════════════════════════════════════════════════ -->
  <!-- Left tail front strand (over shoulder) -->
  <path d="M150 165 Q132 190 120 235 Q110 270 108 310 Q106 345 110 370 Q114 388 122 385 Q126 370 126 340 Q128 300 134 260 Q142 220 152 185 Z"
        fill="url(#tailGradInner)"/>
  <path d="M146 180 Q126 210 114 255 Q104 290 102 330 Q101 360 106 385 Q110 398 116 395 Q118 378 118 350 Q120 310 126 268 Q134 225 148 190 Z"
        fill="url(#tailTip)" opacity="0.38"/>
  <!-- Inner strand -->
  <path d="M144 175 Q128 200 118 240 Q112 270 110 305 Q109 330 112 350 L116 347 Q115 328 116 302 Q120 268 128 238 Q136 208 146 188 Z"
        fill="#e8f0f8" opacity="0.3"/>

  <!-- Right tail front strand (over shoulder) -->
  <path d="M290 165 Q308 190 320 235 Q330 270 332 310 Q334 345 330 370 Q326 388 318 385 Q314 370 314 340 Q312 300 306 260 Q298 220 288 185 Z"
        fill="url(#tailGradInner)"/>
  <path d="M294 180 Q314 210 326 255 Q336 290 338 330 Q339 360 334 385 Q330 398 324 395 Q322 378 322 350 Q320 310 314 268 Q306 225 292 190 Z"
        fill="url(#tailTip)" opacity="0.38"/>
  <path d="M296 175 Q312 200 322 240 Q328 270 330 305 Q331 330 328 350 L324 347 Q325 328 324 302 Q320 268 312 238 Q304 208 294 188 Z"
        fill="#e8f0f8" opacity="0.3"/>

  <!-- ═══════════════════════════════════════════════════
       EYES — gray-blue, narrower, kuudere expression
       ═══════════════════════════════════════════════════ -->
  <!-- LEFT EYE -->
  <g id="eva-left-eye" transform="translate(190, 114)">
    <ellipse cx="0" cy="2" rx="16" ry="11" fill="#e0c8b8" opacity="0.2"/>
    <!-- Upper lash line — subtle downward outer corner -->
    <path d="M-17 0 Q-13 -8 -6 -11 Q0 -12 7 -10 Q13 -6 16 1 Q8 -6 2 -7 Q-6 -8 -11 -4 Q-16 0 -17 0 Z"
          fill="#2a1a10" opacity="0.15"/>
    <path d="M-17 0 Q-13 -8 -6 -11 Q0 -12 7 -10 Q13 -6 16 1"
          fill="none" stroke="#2a1a10" stroke-width="2.5" stroke-linecap="round"/>
    <!-- Eye white -->
    <ellipse cx="0" cy="2" rx="14" ry="9" fill="#f8fcfd"/>
    <ellipse cx="0" cy="2" rx="14" ry="9" fill="#d8e0e4" opacity="0.12"/>
    <!-- Iris — gray-blue -->
    <g id="eva-left-iris">
      <circle cx="0" cy="3" r="10.5" fill="url(#eyeBlue)"/>
      <!-- Pupil -->
      <circle cx="1" cy="4" r="5.5" fill="#181820"/>
      <!-- Iris inner ring detail -->
      <circle cx="0" cy="3" r="8.5" fill="none" stroke="#9aaccc" stroke-width="0.6" opacity="0.35"/>
      <!-- Main catchlight (small, upper-right) -->
      <ellipse cx="4" cy="-2" rx="3.5" ry="3" fill="#ffffff" opacity="0.85"/>
      <!-- Secondary catchlight (tiny, lower-left) -->
      <circle cx="-5" cy="7" r="1.5" fill="#ffffff" opacity="0.4"/>
    </g>
    <!-- Upper lashes (long but subtle) -->
    <path d="M-13 -3 L-14 -7 M-8 -8 L-9 -12 M-2 -10 L-3 -14 M4 -9 L3 -13 M10 -5 L9 -9"
          fill="none" stroke="#2a1a10" stroke-width="1.6" stroke-linecap="round" opacity="0.7"/>
    <!-- Lower lash line (very faint) -->
    <path d="M-14 3 Q-8 8 -1 9 Q5 9 10 7 Q14 5 15 2"
          fill="none" stroke="#b89888" stroke-width="0.8" opacity="0.4"/>
    <!-- Blink eyelid -->
    <path d="M-19 -2 Q-15 -14 0 -16 Q15 -14 19 -2 Q15 -10 0 -12 Q-15 -10 -19 -2 Z"
          fill="url(#skinBase)" class="eva-eyelid"
          style="transform-origin:0 0;animation:avatar-blink-keyframes 5.5s infinite;"/>
    <!-- Lower rim -->
    <path d="M-14 3 Q-7 10 2 10 Q8 9 13 6"
          fill="none" stroke="#d0b8a8" stroke-width="0.6" opacity="0.28"/>
  </g>

  <!-- RIGHT EYE -->
  <g id="eva-right-eye" transform="translate(250, 114)">
    <ellipse cx="0" cy="2" rx="16" ry="11" fill="#e0c8b8" opacity="0.2"/>
    <path d="M17 0 Q13 -8 6 -11 Q0 -12 -7 -10 Q-13 -6 -16 1 Q-8 -6 -2 -7 Q6 -8 11 -4 Q16 0 17 0 Z"
          fill="#2a1a10" opacity="0.15"/>
    <path d="M17 0 Q13 -8 6 -11 Q0 -12 -7 -10 Q-13 -6 -16 1"
          fill="none" stroke="#2a1a10" stroke-width="2.5" stroke-linecap="round"/>
    <ellipse cx="0" cy="2" rx="14" ry="9" fill="#f8fcfd"/>
    <ellipse cx="0" cy="2" rx="14" ry="9" fill="#d8e0e4" opacity="0.12"/>
    <g id="eva-right-iris">
      <circle cx="0" cy="3" r="10.5" fill="url(#eyeBlue)"/>
      <circle cx="-1" cy="4" r="5.5" fill="#181820"/>
      <circle cx="0" cy="3" r="8.5" fill="none" stroke="#9aaccc" stroke-width="0.6" opacity="0.35"/>
      <ellipse cx="4" cy="-2" rx="3.5" ry="3" fill="#ffffff" opacity="0.85"/>
      <circle cx="-5" cy="7" r="1.5" fill="#ffffff" opacity="0.4"/>
    </g>
    <path d="M13 -3 L14 -7 M8 -8 L9 -12 M2 -10 L3 -14 M-4 -9 L-3 -13 M-10 -5 L-9 -9"
          fill="none" stroke="#2a1a10" stroke-width="1.6" stroke-linecap="round" opacity="0.7"/>
    <path d="M14 3 Q8 8 1 9 Q-5 9 -10 7 Q-14 5 -15 2"
          fill="none" stroke="#b89888" stroke-width="0.8" opacity="0.4"/>
    <path d="M19 -2 Q15 -14 0 -16 Q-15 -14 -19 -2 Q-15 -10 0 -12 Q15 -10 19 -2 Z"
          fill="url(#skinBase)" class="eva-eyelid"
          style="transform-origin:0 0;animation:avatar-blink-keyframes 5.5s infinite;"/>
    <path d="M14 3 Q7 10 -2 10 Q-8 9 -13 6"
          fill="none" stroke="#d0b8a8" stroke-width="0.6" opacity="0.28"/>
  </g>

  <!-- ═══════════════════════════════════════════════════
       NOSE — very subtle
       ═══════════════════════════════════════════════════ -->
  <path d="M221 112 L220 128"
        fill="none" stroke="#d0b8a0" stroke-width="0.6" opacity="0.2"/>
  <path d="M219 128 Q220 126 221 128"
        fill="none" stroke="#c4a890" stroke-width="1" stroke-linecap="round" opacity="0.3"/>
  <circle cx="213" cy="140" r="1.2" fill="#c0a088" opacity="0.18"/>
  <circle cx="227" cy="140" r="1.2" fill="#c0a088" opacity="0.18"/>

  <!-- ═══════════════════════════════════════════════════
       MOUTH — very small, pale
       ═══════════════════════════════════════════════════ -->
  <path d="M214 152 Q217 149 220 149 Q223 149 226 152"
        fill="none" stroke="#c49890" stroke-width="1" stroke-linecap="round" opacity="0.45"/>
  <path id="eva-mouth"
        d="M209 156 Q214 154 220 154 Q226 154 231 156"
        fill="none" stroke="#c89890" stroke-width="1.6" stroke-linecap="round" opacity="0.55"/>
  <path d="M212 158 Q220 163 228 158"
        fill="none" stroke="#c89890" stroke-width="0.8" stroke-linecap="round" opacity="0.22"/>

  <!-- ═══════════════════════════════════════════════════
       COLLARBONE
       ═══════════════════════════════════════════════════ -->
  <path d="M196 200 Q210 206 220 207 Q230 206 244 200"
        fill="none" stroke="#c8b09c" stroke-width="0.7" opacity="0.18"/>

  <!-- ═══════════════════════════════════════════════════
       HAIR ORNAMENTS — large black crosses, purple-pink outline
       ═══════════════════════════════════════════════════ -->
  <!-- Left cross ornament -->
  <g transform="translate(158, 118)">
    <!-- Cross glow -->
    <path d="M-2 -14 L2 -14 L2 -4 L10 -4 L10 1 L2 1 L2 14 L-2 14 L-2 1 L-10 1 L-10 -4 L-2 -4 Z"
          fill="none" stroke="url(#crossStroke)" stroke-width="2.5" opacity="0.7"/>
    <!-- Cross fill -->
    <path d="M-2 -14 L2 -14 L2 -4 L10 -4 L10 1 L2 1 L2 14 L-2 14 L-2 1 L-10 1 L-10 -4 L-2 -4 Z"
          fill="#161616"/>
    <!-- Cross inner highlight -->
    <path d="M-1 -12 L1 -12 L1 -3 L8 -3 L8 0 L1 0 L1 12 L-1 12 L-1 0 L-8 0 L-8 -3 L-1 -3 Z"
          fill="#2a2a2a" opacity="0.5"/>
  </g>

  <!-- Right cross ornament -->
  <g transform="translate(282, 118)">
    <path d="M-2 -14 L2 -14 L2 -4 L10 -4 L10 1 L2 1 L2 14 L-2 14 L-2 1 L-10 1 L-10 -4 L-2 -4 Z"
          fill="none" stroke="url(#crossStroke)" stroke-width="2.5" opacity="0.7"/>
    <path d="M-2 -14 L2 -14 L2 -4 L10 -4 L10 1 L2 1 L2 14 L-2 14 L-2 1 L-10 1 L-10 -4 L-2 -4 Z"
          fill="#161616"/>
    <path d="M-1 -12 L1 -12 L1 -3 L8 -3 L8 0 L1 0 L1 12 L-1 12 L-1 0 L-8 0 L-8 -3 L-1 -3 Z"
          fill="#2a2a2a" opacity="0.5"/>
  </g>

  <!-- ═══════════════════════════════════════════════════
       HAIR — HIGHLIGHT SWEEPS ON TWIN TAILS
       ═══════════════════════════════════════════════════ -->
  <path d="M150 140 Q135 220 125 320 Q118 400 120 470"
        fill="none" stroke="#ffffff" stroke-width="2.8" stroke-linecap="round" opacity="0.1"/>
  <path d="M155 135 Q142 210 132 310 Q126 390 128 460"
        fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" opacity="0.07"/>
  <path d="M290 140 Q305 220 315 320 Q322 400 320 470"
        fill="none" stroke="#ffffff" stroke-width="2.8" stroke-linecap="round" opacity="0.1"/>
  <path d="M285 135 Q298 210 308 310 Q314 390 312 460"
        fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" opacity="0.07"/>

  <!-- Flyaway strands -->
  <path d="M155 70 Q148 64 142 54 Q140 46 144 40"
        fill="none" stroke="#e4ecf4" stroke-width="1.5" stroke-linecap="round" opacity="0.45"/>
  <path d="M162 58 Q158 48 156 38"
        fill="none" stroke="#d8e4f0" stroke-width="1.1" stroke-linecap="round" opacity="0.35"/>
  <path d="M278 68 Q284 60 288 50 Q290 42 288 36"
        fill="none" stroke="#e4ecf4" stroke-width="1.5" stroke-linecap="round" opacity="0.45"/>
  <path d="M272 56 Q276 44 280 34"
        fill="none" stroke="#d8e4f0" stroke-width="1.1" stroke-linecap="round" opacity="0.35"/>

  <!-- Over-chest hair strands -->
  <path d="M148 200 Q136 230 130 270 Q126 300 128 325"
        fill="none" stroke="#d0ddf0" stroke-width="1.8" stroke-linecap="round" opacity="0.3"/>
  <path d="M292 200 Q304 230 310 270 Q314 300 312 325"
        fill="none" stroke="#d0ddf0" stroke-width="1.8" stroke-linecap="round" opacity="0.3"/>
</svg>`;
  },
};
