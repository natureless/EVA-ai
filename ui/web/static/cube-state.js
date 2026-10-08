/*
 * Exact, renderer-independent 2×2×2 / 3×3×3 cube state.
 *
 * Coordinates follow CSS 3D: +x right, +y down, +z toward the viewer.
 * Every cubie has a stable identity, a position in {-1, +1}³ (order 2)
 * or {-1, 0, +1}³ excluding the hidden core (order 3), and a
 * row-major 3×3 orientation mapping local coordinates into world coordinates.
 * Orientation and position use integers only; animated interpolation belongs
 * to the renderer and must never be committed to this state.
 *
 * Moves are clockwise when viewed from outside the named face. With the
 * standard CSS rotateX/Y/Z matrices this means R+, L−, U−, D+, F+, B−.
 * A prime reverses a quarter-turn; “2” is a positive 180° half-turn.
 * Rotations act in world space: newOrientation = rotation × orientation.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CubeLogic = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const FACE = {
    R: { axis: 'x', layer: 1, turns: 1 },
    L: { axis: 'x', layer: -1, turns: -1 },
    U: { axis: 'y', layer: -1, turns: -1 },
    D: { axis: 'y', layer: 1, turns: 1 },
    F: { axis: 'z', layer: 1, turns: 1 },
    B: { axis: 'z', layer: -1, turns: -1 },
  };
  const AXIS_INDEX = { x: 0, y: 1, z: 2 };
  const FACES = Object.keys(FACE);
  const SUFFIXES = ['', "'", '2'];
  function initialCubies(order) {
    const initial = [];
    const coordinates = order === 2 ? [-1, 1] : [-1, 0, 1];
    for (const x of coordinates) {
      for (const y of coordinates) {
        for (const z of coordinates) {
          if (x === 0 && y === 0 && z === 0) continue;
          initial.push({ id: 'cubie-' + initial.length, position: [x, y, z] });
        }
      }
    }
    return { cubies: initial, byId: new Map(initial.map(cubie => [cubie.id, cubie])) };
  }
  const INITIAL_BY_ORDER = new Map([2, 3].map(order => [order, initialCubies(order)]));

  function parseMove(move) {
    if (typeof move !== 'string' || !/^[RLUDFB](?:'|2)?$/.test(move)) {
      throw new TypeError('Move must be R, L, U, D, F or B, optionally followed by a prime or 2.');
    }
    const face = move[0];
    const definition = FACE[face];
    const turns = move[1] === '2' ? 2 : definition.turns * (move[1] === "'" ? -1 : 1);
    return { face, axis: definition.axis, layer: definition.layer, turns, angle: turns * 90 };
  }

  function invertMove(move) {
    parseMove(move);
    return move[1] === '2' ? move : move[1] === "'" ? move[0] : move + "'";
  }

  function inverseSequence(sequence) {
    if (!Array.isArray(sequence)) throw new TypeError('Move sequence must be an array.');
    // Array.from also rejects sparse entries instead of silently skipping them.
    return Array.from(sequence, invertMove).reverse();
  }

  function multiply(a, b) {
    const result = new Array(9).fill(0);
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        for (let k = 0; k < 3; k++) result[row * 3 + col] += a[row * 3 + k] * b[k * 3 + col];
      }
    }
    return result;
  }

  function transform(matrix, vector) {
    return [0, 1, 2].map(row =>
      // Normalize signed zero as well: an exact state has one canonical zero.
      (matrix[row * 3] * vector[0] + matrix[row * 3 + 1] * vector[1] + matrix[row * 3 + 2] * vector[2]) || 0);
  }

  function rotationMatrix(axis, turns) {
    const positiveQuarter = axis === 'x' ? [1, 0, 0, 0, 0, -1, 0, 1, 0]
      : axis === 'y' ? [0, 0, 1, 0, 1, 0, -1, 0, 0]
        : [0, -1, 0, 1, 0, 0, 0, 0, 1];
    let result = IDENTITY.slice();
    for (let i = 0, count = (turns + 4) % 4; i < count; i++) result = multiply(positiveQuarter, result);
    return result;
  }

  class CubeState {
    constructor(order = 2) {
      if (order !== 2 && order !== 3) throw new RangeError('Cube order must be 2 or 3.');
      // Changing order requires a new cube, not reinterpreting existing pieces.
      Object.defineProperty(this, 'order', { value: order, enumerable: true });
      this.cubies = INITIAL_BY_ORDER.get(order).cubies.map(cubie => ({
        id: cubie.id,
        position: cubie.position.slice(),
        orientation: IDENTITY.slice(),
      }));
    }

    snapshot() {
      return { order: this.order, cubies: this.cubies.map(cubie => ({
        id: cubie.id,
        position: cubie.position.slice(),
        orientation: cubie.orientation.slice(),
      })) };
    }

    isSolved() {
      const template = INITIAL_BY_ORDER.get(this.order);
      return this.cubies.length === template.cubies.length
        && new Set(this.cubies.map(cubie => cubie.id)).size === template.cubies.length
        && this.cubies.every(cubie => {
          const initial = template.byId.get(cubie.id);
          return initial && cubie.position.length === 3 && cubie.orientation.length === 9
            && cubie.position.every((value, index) => value === initial.position[index])
            && cubie.orientation.every((value, index) => value === IDENTITY[index]);
        });
    }

    apply(move) {
      const parsed = parseMove(move);
      const matrix = rotationMatrix(parsed.axis, parsed.turns);
      const affected = this.cubies.filter(cubie => cubie.position[AXIS_INDEX[parsed.axis]] === parsed.layer);
      if (affected.length !== this.order ** 2) throw new Error(`Invalid cube state: a face must contain exactly ${this.order ** 2} cubies.`);
      affected.forEach(cubie => {
        cubie.position = transform(matrix, cubie.position);
        cubie.orientation = multiply(matrix, cubie.orientation);
      });
      return affected.map(cubie => cubie.id);
    }
  }

  function generateScramble(length = 10, rng = Math.random) {
    if (!Number.isSafeInteger(length) || length < 0) throw new RangeError('Scramble length must be a non-negative safe integer.');
    if (typeof rng !== 'function') throw new TypeError('rng must be a function returning a number in [0, 1).');
    function choose(values) {
      const sample = rng();
      if (typeof sample !== 'number' || !Number.isFinite(sample) || sample < 0 || sample >= 1) {
        throw new RangeError('rng must return a number in [0, 1).');
      }
      return values[Math.floor(sample * values.length)];
    }
    const sequence = [];
    let previousAxis = null;
    for (let i = 0; i < length; i++) {
      const face = choose(FACES.filter(candidate => FACE[candidate].axis !== previousAxis));
      sequence.push(face + choose(SUFFIXES));
      previousAxis = FACE[face].axis;
    }
    return sequence;
  }

  return Object.freeze({ CubeState, parseMove, invertMove, inverseSequence, generateScramble });
});
