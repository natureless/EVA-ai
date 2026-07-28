// EVA Particle Controller — canvas-based particle effects replacing SVG character
// States: idle (slow drift), listening (cursor-attract), responding (burst + energy rings)
const ParticleController = {
  _state: "idle",
  _canvas: null,
  _particles: [],
  _animRAF: null,
  _mouseX: 0, _mouseY: 0,
  _targetMouseX: 0, _targetMouseY: 0,
  _ringTimers: [],
  _entranceDone: false,

  init(containerId, canvasId) {
    this._canvas = document.getElementById(canvasId);
    if (!this._canvas) return;

    this._bindMouseTracking();
    this._initParticles();
    this._entranceDone = true;
    this._setIdle();
  },

  // ═══════════════════════════════════════════════════════════
  // MOUSE TRACKING — for cursor-attract in listening state
  // ═══════════════════════════════════════════════════════════
  _bindMouseTracking() {
    const panel = document.getElementById("avatarPanel");
    const target = panel || document;
    target.addEventListener("mousemove", (e) => {
      const rect = this._canvas.getBoundingClientRect();
      this._targetMouseX = (e.clientX - rect.left) / rect.width;
      this._targetMouseY = (e.clientY - rect.top) / rect.height;
    });
    target.addEventListener("mouseleave", () => {
      this._targetMouseX = 0.5;
      this._targetMouseY = 0.5;
    });
  },

  // ═══════════════════════════════════════════════════════════
  // UNIFIED ANIMATION LOOP
  // ═══════════════════════════════════════════════════════════
  _startAnimLoop() {
    if (this._animRAF) return;
    const loop = () => {
      this._particleStep();
      this._animRAF = requestAnimationFrame(loop);
    };
    this._animRAF = requestAnimationFrame(loop);
  },

  // ═══════════════════════════════════════════════════════════
  // STATE MACHINE — public API unchanged for chat.js compat
  // ═══════════════════════════════════════════════════════════

  _setIdle() {
    this._state = "idle";
    this._setParticles("calm");
    this._updateLabel("avatar.idle");
    this._startAnimLoop();
  },

  setListening() {
    if (this._state === "responding") return;
    this._state = "listening";
    this._setParticles("active");
    this._updateLabel("avatar.listening");
    this._startAnimLoop();
  },

  setResponding() {
    this._state = "responding";
    this._setParticles("active");
    this.burstParticles(12);
    this._updateLabel("avatar.responding");
    this._startAnimLoop();
  },

  // ═══════════════════════════════════════════════════════════
  // PUBLIC — burst particles (called by chat.js on tokens)
  // ═══════════════════════════════════════════════════════════
  burstParticles(count) {
    const c = this._canvas;
    if (!c) return;
    const cx = c.width / 2;
    const cy = c.height * 0.4;
    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = 30 + Math.random() * 80;
      const isRing = this._state === "responding" && Math.random() < 0.25;
      this._particles.push({
        x: cx + Math.cos(angle) * 8,
        y: cy + Math.sin(angle) * 8,
        r: isRing ? 0 : 1.2 + Math.random() * 3.5,
        speed: -1.5 - Math.random() * 2.5,
        drift: Math.cos(angle) * 2.5,
        opacity: 0.6 + Math.random() * 0.4,
        fadeRate: 0.006 + Math.random() * 0.014,
        hue: Math.random() < 0.5 ? "teal" : "accent",
        shape: isRing ? "ring" : "circle",
        glow: true,
        life: 1,
        burst: true,
        ringRadius: isRing ? 3 + Math.random() * 5 : 0,
        ringMax: isRing ? 25 + Math.random() * 35 : 0,
      });
    }
  },

  _updateLabel(key) {
    const el = document.getElementById("avatarStateLabel");
    if (el && I18N) { el.textContent = I18N.t(key); }
  },

  destroy() {
    if (this._animRAF) cancelAnimationFrame(this._animRAF);
    this._animRAF = null;
  },

  // ═══════════════════════════════════════════════════════════
  // PARTICLE SYSTEM
  // ═══════════════════════════════════════════════════════════

  _initParticles() {
    const c = this._canvas;
    if (!c) return;
    this._resizeCanvas();
    this._spawnParticles(18);
    window.addEventListener("resize", () => this._resizeCanvas());
  },

  _resizeCanvas() {
    const c = this._canvas;
    if (!c || !c.parentElement) return;
    const rect = c.parentElement.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
  },

  _spawnParticles(count) {
    const c = this._canvas;
    if (!c) return;
    for (let i = 0; i < count; i++) {
      const hueRoll = Math.random();
      const hue = hueRoll < 0.45 ? "accent" : hueRoll < 0.7 ? "teal" : "white";
      this._particles.push({
        x: Math.random() * c.width,
        y: Math.random() * c.height,
        r: 0.5 + Math.random() * 2.5,
        speed: 0.15 + Math.random() * 0.8,
        drift: (Math.random() - 0.5) * 0.4,
        opacity: 0.1 + Math.random() * 0.4,
        fadeRate: 0.0004 + Math.random() * 0.0015,
        hue,
        shape: Math.random() < 0.2 ? "diamond" : "circle",
        glow: Math.random() < 0.25,
        life: Math.random(),
        burst: false,
        ringRadius: 0,
        ringMax: 0,
      });
    }
  },

  _setParticles(mode) {
    const target = mode === "active" ? 40 : 18;
    const diff = target - this._particles.length;
    if (diff > 0) this._spawnParticles(diff);
    if (diff < 0) this._particles.length = target;
    this._particles.forEach(p => {
      if (!p.burst) {
        p.speed = mode === "active" ? 0.3 + Math.random() * 1.5 : 0.15 + Math.random() * 0.8;
      }
    });
  },

  _particleStep() {
    const c = this._canvas;
    if (!c) return;
    const ctx = c.getContext("2d");
    const w = c.width, h = c.height;

    ctx.clearRect(0, 0, w, h);

    const style = getComputedStyle(document.documentElement);
    const accentColor = style.getPropertyValue("--accent").trim() || "#0f766e";
    const accentLight = style.getPropertyValue("--accent-light").trim() || "#ccfbf1";

    // Smooth mouse position lerp
    this._mouseX += (this._targetMouseX - this._mouseX) * 0.06;
    this._mouseY += (this._targetMouseY - this._mouseY) * 0.06;

    const cx = w / 2;
    const cy = h * 0.4;
    const attractX = this._mouseX * w;
    const attractY = this._mouseY * h;

    const len = this._particles.length;
    for (let i = len - 1; i >= 0; i--) {
      const p = this._particles[i];

      // ── move ──────────────────────────────────────────
      p.y -= p.speed;
      p.x += p.drift;
      p.opacity -= p.fadeRate;
      p.life -= p.fadeRate;

      // ── cursor attract in listening/responding ───────
      if (this._state === "listening" && !p.burst) {
        const dx = attractX - p.x;
        const dy = attractY - p.y;
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        const force = 0.3 / dist;
        p.x += dx * force * 2;
        p.y += dy * force * 2;
      }

      // ── responding: ambient drift toward center ──────
      if (this._state === "responding" && !p.burst) {
        const dx = cx - p.x;
        const dy = cy - p.y;
        p.x += dx * 0.003;
        p.y += dy * 0.003;
      }

      // ── ring expansion ───────────────────────────────
      if (p.shape === "ring" && p.burst) {
        p.ringRadius += (p.ringMax - p.ringRadius) * 0.12;
        p.opacity -= p.fadeRate * 0.4;
      }

      // ── off-screen / dead cleanup ────────────────────
      if (p.y < -20 || p.opacity <= 0 || (p.burst && p.life <= 0)) {
        this._particles.splice(i, 1);
        continue;
      }

      // ── color ────────────────────────────────────────
      let color;
      if (p.hue === "accent") color = accentColor;
      else if (p.hue === "teal") color = "#2dd4bf";
      else color = accentLight;

      // ── draw ─────────────────────────────────────────
      ctx.save();
      ctx.globalAlpha = Math.max(0, p.opacity);

      if (p.shape === "ring") {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.ringRadius, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.shadowColor = color;
        ctx.shadowBlur = 8;
        ctx.stroke();
        ctx.shadowBlur = 0;
      } else if (p.glow) {
        ctx.shadowColor = color;
        ctx.shadowBlur = p.burst ? 12 : 6;
        if (p.shape === "diamond") {
          ctx.beginPath();
          ctx.moveTo(p.x, p.y - p.r * 1.2);
          ctx.lineTo(p.x + p.r, p.y);
          ctx.lineTo(p.x, p.y + p.r * 1.2);
          ctx.lineTo(p.x - p.r, p.y);
          ctx.closePath();
          ctx.fillStyle = color;
          ctx.fill();
        } else {
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
        }
        ctx.shadowBlur = 0;
      } else {
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
      }

      ctx.restore();
    }

    // Replenish ambient particles
    const target = this._state === "responding" ? 40 : this._state === "listening" ? 30 : 18;
    const nonBurst = this._particles.filter(p => !p.burst).length;
    if (nonBurst < target && Math.random() < 0.35) {
      this._spawnParticles(1);
    }
    if (this._state === "responding" && Math.random() < 0.08) {
      this.burstParticles(2);
    }
  },
};
