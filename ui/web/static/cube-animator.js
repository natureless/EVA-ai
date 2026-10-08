// CSS 3D geometry and interpolation only. Logical state is owned by CubeLogo.
(function (root, factory) {
  const brand = typeof module === "object" && module.exports ? require("./logo-tokens.js") : root.EvaBrand;
  const api = factory(brand);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CubeRendering = api;
})(globalThis, function (brand) {
  "use strict";
  const FACES = Object.freeze({
    front: [2, 1], back: [2, -1], right: [0, 1], left: [0, -1], top: [1, -1], bottom: [1, 1],
  });
  // Both orders occupy a 128px envelope. Logical coordinates remain ±1
  // (and 0 for order 3), so their pixel spacing differs by order. Values
  // come from the brand system in both browsers and CommonJS.
  const GEOMETRIES = Object.freeze(Object.fromEntries([2, 3].map(order => {
    const g = brand.geometry(order);
    return [order, Object.freeze({size:g.cubieSize, gap:g.gap, radius:g.radius, spacing:g.spacing, faces:FACES})];
  })));
  function geometryFor(order = 2) {
    if (order !== 2 && order !== 3) throw new RangeError("Cube order must be 2 or 3.");
    return GEOMETRIES[order];
  }
  const Geometry = geometryFor(2);
  // Small, repeatable timing differences give the motion a calm cadence. The
  // controller still owns the baseline duration (including the slower final
  // solve move); this profile affects presentation, never the move sequence.
  const MOVE_CADENCE = brand.tokens.motion.cadence;

  function transform(cubie, geometry = Geometry) {
    const m = cubie.orientation;
    const [x, y, z] = cubie.position.map(value => value * geometry.spacing);
    // CSS matrix3d is column-major; logical orientation is row-major.
    return `translate3d(${x}px, ${y}px, ${z}px) matrix3d(${[
      m[0], m[3], m[6], 0, m[1], m[4], m[7], 0, m[2], m[5], m[8], 0, 0, 0, 0, 1,
    ].join(",")})`;
  }

  class CubeAnimator {
    constructor(root, state) {
      this.root = root;
      this.document = root.ownerDocument;
      this.geometry = geometryFor(state.order);
      this.nodes = new Map();
      this.active = null;
      this.layer = null;
      this.paused = false;
      this.reduced = false;
      this._cadenceIndex = 0;
      this.root.style.setProperty("--cube-size", `${this.geometry.size}px`);
      this.root.style.setProperty("--cube-half", `${this.geometry.size / 2}px`);
      this.root.style.setProperty("--cube-gap", `${this.geometry.gap}px`);
      this.root.style.setProperty("--cube-radius", `${this.geometry.radius}px`);
      for (const cubie of state.cubies) {
        const node = this.document.createElement("div");
        node.className = "cubie";
        node.dataset.cubieId = cubie.id;
        for (const [name, [axis, side]] of Object.entries(this.geometry.faces)) {
          const face = this.document.createElement("div");
          // Corners/edges/centers have 3/2/1 external stickers respectively.
          face.className = `cube-face ${name}${cubie.position[axis] === side ? "" : " internal"}`;
          node.appendChild(face);
        }
        this.root.appendChild(node);
        this.nodes.set(cubie.id, node);
      }
      this.render(state);
    }

    render(state) {
      for (const cubie of state.cubies) {
        const node = this.nodes.get(cubie.id);
        node.style.transform = transform(cubie, this.geometry);
        node.dataset.position = cubie.position.join(",");
        node.dataset.orientation = cubie.orientation.join(",");
      }
    }

    async _animate(element, frames, duration) {
      if (this.reduced || duration === 0) return;
      const effect = element.animate(frames, { duration, easing: "cubic-bezier(.4,0,.2,1)", fill: "forwards" });
      this.active = effect;
      if (this.paused) effect.pause();
      try { await effect.finished; }
      finally {
        if (this.active === effect) this.active = null;
        // A layer effect is retained until commit reparents its children atomically.
        if (element !== this.layer) effect.cancel();
      }
      return effect;
    }

    async turn(state, move, duration) {
      if (this.layer) throw new Error("A cube layer is already rotating");
      const axis = { x: 0, y: 1, z: 2 }[move.axis];
      const selected = state.cubies.filter(cubie => cubie.position[axis] === move.layer);
      if (selected.length !== state.order ** 2) throw new Error(`A legal layer must contain ${state.order ** 2} cubies`);
      const layer = this.document.createElement("div");
      layer.className = "cube-turn-layer";
      layer.dataset.move = move.face;
      this.root.appendChild(layer);
      this.layer = layer;
      for (const cubie of selected) layer.appendChild(this.nodes.get(cubie.id));
      const rotation = `rotate${move.axis.toUpperCase()}`;
      const cadence = MOVE_CADENCE[this._cadenceIndex];
      this._cadenceIndex = (this._cadenceIndex + 1) % MOVE_CADENCE.length;
      const motionDuration = Math.round(duration * cadence * (Math.abs(move.turns) === 2 ? 1.2 : 1));
      try {
        this.layerEffect = await this._animate(layer, [
          { transform: `${rotation}(0deg)` }, { transform: `${rotation}(${move.angle}deg)` },
        ], motionDuration);
      } catch (error) {
        this.commit(state); // Cancelled interpolation never changes the logical state.
        throw error;
      }
    }

    commit(state) {
      // No reads from computed transforms: the committed integer model is authoritative.
      for (const node of this.nodes.values()) this.root.appendChild(node);
      this.render(state);
      this.layerEffect?.cancel();
      this.layerEffect = null;
      this.layer?.remove();
      this.layer = null;
    }

    wait(duration) { return this._animate(this.root, [{}, {}], duration); }
    pause() { this.paused = true; this.active?.pause(); }
    resume() { this.paused = false; this.active?.play(); }
    setReducedMotion(reduced) {
      this.reduced = reduced;
      // Finish only the in-flight legal move. The controller then unwinds its history.
      if (reduced && this.active) this.active.finish();
    }
    dispose() {
      this.active?.cancel();
      this.layerEffect?.cancel();
      this.active = this.layerEffect = null;
      for (const node of this.nodes.values()) node.remove();
      this.nodes.clear();
      this.layer?.remove();
      this.layer = null;
    }
  }
  return { Geometry, geometryFor, transform, CubeAnimator };
});
