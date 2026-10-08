// One serialized action queue; every restoration is the inverse of committed moves.
(function (root, factory) {
  const logic = typeof module === "object" && module.exports ? require("./cube-state.js") : root.CubeLogic;
  const brand = typeof module === "object" && module.exports ? require("./logo-tokens.js") : root.EvaBrand;
  const api = factory(logic, brand);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CubeLogoSystem = api;
})(globalThis, function (logic, brand) {
  "use strict";
  const { CubeState, parseMove, inverseSequence, generateScramble, invertMove } = logic;
  const timing = brand.tokens.motion;
  const defaults = timing.modes.normal;

  class CubeLogo {
    constructor({ animator, state = new CubeState(), scrambleLength = defaults.scrambleLength, moveDuration = defaults.moveDuration,
      solveDuration = defaults.solveDuration, loop = true, rng = Math.random, onChange = () => {} } = {}) {
      if (!animator) throw new Error("CubeLogo requires an animator");
      if (!Number.isInteger(scrambleLength) || scrambleLength < 1 || scrambleLength > 100) throw new RangeError("Invalid scramble length");
      if (![moveDuration, solveDuration].every(value => Number.isFinite(value) && value >= 0)) throw new RangeError("Invalid move duration");
      this.animator = animator;
      this.state = state;
      this.scrambleLength = scrambleLength;
      this.moveDuration = moveDuration;
      this.solveDuration = solveDuration;
      this.loop = loop;
      this.rng = rng;
      this.onChange = onChange;
      this.stage = "idle";
      this.history = [];
      this.scrambleMoves = [];
      this.solveMoves = [];
      this.completedCycles = 0;
      this._pauseReasons = new Set();
      this._task = null;
      this._recovery = null;
      this._stopRequested = false;
      this._reduced = false;
      this._loop = false;
      this.progress = { move: "", index: 0, total: 0 };
    }

    get busy() { return this._task !== null || this._recovery !== null; }
    get paused() { return this._pauseReasons.size > 0; }
    _emit() {
      this.onChange({ stage: this.stage, ...this.progress, paused: this.paused, solved: this.state.isSolved(),
        scrambleMoves: [...this.scrambleMoves], solveMoves: [...this.solveMoves], completedCycles: this.completedCycles });
    }
    _stage(stage, progress = {}) {
      this.stage = stage;
      this.progress = { move: "", index: 0, total: 0, ...progress };
      this._emit();
    }
    _run(action) {
      if (this._task) return this._task;
      this._stopRequested = false;
      this._task = Promise.resolve().then(action).catch(error => {
        this._loop = false;
        this._stage("error");
        throw error;
      }).finally(() => { this._task = null; });
      return this._task;
    }

    async _move(move, duration, journal) {
      const parsed = parseMove(move);
      await this.animator.turn(this.state, parsed, this._reduced ? 0 : duration);
      this.state.apply(move);
      journal();
      this.animator.commit?.(this.state);
    }

    async _scramble() {
      this.scrambleMoves = generateScramble(this.scrambleLength, this.rng);
      this.solveMoves = inverseSequence(this.scrambleMoves);
      for (let i = 0; i < this.scrambleMoves.length && !this._stopRequested; i++) {
        const move = this.scrambleMoves[i];
        this._stage("scrambling", { move, index: i + 1, total: this.scrambleMoves.length });
        await this._move(move, this.moveDuration, () => this.history.push(move));
        this._emit();
      }
    }

    async _restore() {
      const moves = inverseSequence(this.history);
      this.solveMoves = moves;
      for (let i = 0; i < moves.length; i++) {
        const move = moves[i];
        // Read from the journal; never assign initial coordinates to fake restoration.
        if (move !== invertMove(this.history[this.history.length - 1])) throw new Error("Cube move journal is inconsistent");
        this._stage("solving", { move, index: i + 1, total: moves.length });
        await this._move(move, i === moves.length - 1 ? this.moveDuration : this.solveDuration, () => this.history.pop());
        this._emit();
      }
      if (!this.state.isSolved()) throw new Error("Inverse sequence did not restore the cube");
    }

    async _settle() {
      this._stage("settling");
      await this.animator.wait(this._reduced ? timing.reduced : timing.settle);
      this._stage("idle");
    }

    play({ loop = this.loop } = {}) {
      if (this._recovery) return this._recovery;
      if (this.stage === "error") return Promise.reject(new Error("Recover the cube before playing again"));
      if (this._reduced) return Promise.resolve();
      if (this._task) return this._task;
      this._loop = loop;
      return this._run(async () => {
        if (this.history.length) await this._restore();
        do {
          this._stage("intro");
          await this.animator.wait(this._reduced ? timing.reduced : timing.intro);
          if (!this._stopRequested) await this._scramble();
          if (!this._stopRequested) {
            this._stage("scrambled");
            await this.animator.wait(this._reduced ? timing.reduced : timing.hold);
          }
          await this._restore();
          this.completedCycles++;
          await this._settle();
        } while (this._loop && !this._stopRequested && !this._reduced);
      });
    }

    scramble() {
      if (this._recovery) return this._recovery;
      if (this.stage === "error") return Promise.reject(new Error("Recover the cube before scrambling again"));
      if (this._reduced) return Promise.resolve();
      return this._run(async () => {
        if (this.history.length) await this._restore();
        await this._scramble();
        if (!this._stopRequested) this._stage("scrambled");
        if (this._stopRequested) { await this._restore(); await this._settle(); }
      });
    }

    solve() {
      this._loop = false;
      this._stopRequested = true;
      this.resume("user");
      if (this._recovery) return this._recovery;
      if (this.stage === "error") return this.recover();
      if (this._task) return this._task;
      return this._run(async () => { await this._restore(); await this._settle(); });
    }
    reset() { return this.solve(); }
    recover({ createAnimator } = {}) {
      if (this._recovery) return this._recovery;
      if (this.stage !== "error") return Promise.reject(new Error("Only a failed cube can be recovered"));
      const pending = this._task;
      this._loop = false;
      this._stopRequested = true;
      // Wait for the failed task to release its animation before rebuilding it.
      this._recovery = Promise.resolve().then(async () => {
        try { await pending; } catch (_) { /* The original failure remains reported to its caller. */ }
        const expected = new CubeState(this.state.order);
        for (const move of this.history) expected.apply(move);
        if (JSON.stringify(expected.snapshot()) !== JSON.stringify(this.state.snapshot())) {
          throw new Error("Cube state does not match its committed move journal");
        }
        if (createAnimator) {
          const replacement = createAnimator(this.state);
          if (!replacement || typeof replacement.turn !== "function" || typeof replacement.wait !== "function") {
            throw new Error("Recovery requires a valid cube animator");
          }
          this.animator.dispose?.();
          this.animator = replacement;
        } else this.animator.commit?.(this.state);
        this.animator.setReducedMotion(this._reduced);
        this._pauseReasons.delete("user");
        if (this.paused) this.animator.pause();
        else this.animator.resume();
        return this._run(async () => { await this._restore(); await this._settle(); });
      }).catch(error => {
        this._stage("error");
        throw error;
      }).finally(() => { this._recovery = null; });
      return this._recovery;
    }
    pause(reason = "user") {
      if (this._pauseReasons.has(reason)) return;
      this._pauseReasons.add(reason);
      this.animator.pause();
      this._emit();
    }
    resume(reason = "user") {
      if (!this._pauseReasons.delete(reason)) return;
      if (!this.paused) this.animator.resume();
      this._emit();
    }
    setReducedMotion(reduced) {
      this._reduced = reduced;
      if (reduced) { this._loop = false; this._stopRequested = true; }
      this.animator.setReducedMotion(reduced);
      if (this.stage === "error" && !this._recovery) return Promise.resolve();
      return reduced ? this.solve() : (this._task || Promise.resolve());
    }
  }
  return { CubeLogo };
});
