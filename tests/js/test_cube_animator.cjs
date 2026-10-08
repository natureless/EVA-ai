const assert = require('node:assert/strict');
const { test } = require('node:test');
const { CubeState, parseMove } = require('../../ui/web/static/cube-state.js');
const { Geometry, geometryFor, transform, CubeAnimator } = require('../../ui/web/static/cube-animator.js');

function harness(order = 2) {
  const effects = [];
  const document = { createElement() { return makeNode(); } };
  function makeNode() {
    return {
      ownerDocument: document, className: '', dataset: {},
      style: { setProperty(name, value) { this[name] = value; } }, children: [], parentNode: null,
      appendChild(child) {
        child.remove();
        this.children.push(child);
        child.parentNode = this;
        return child;
      },
      remove() {
        if (this.parentNode) {
          const siblings = this.parentNode.children;
          siblings.splice(siblings.indexOf(this), 1);
          this.parentNode = null;
        }
      },
      animate(frames, options) {
        let resolve, reject, settled = false;
        const finished = new Promise((yes, no) => { resolve = yes; reject = no; });
        const effect = {
          target: this, frames, options, finished, playState: 'running', calls: [],
          pause() { this.calls.push('pause'); this.playState = 'paused'; },
          play() { this.calls.push('play'); this.playState = 'running'; },
          finish() {
            this.calls.push('finish');
            this.playState = 'finished';
            if (!settled) { settled = true; resolve(); }
          },
          cancel() {
            this.calls.push('cancel');
            this.playState = 'idle';
            if (!settled) {
              settled = true;
              const error = new Error('Animation cancelled');
              error.name = 'AbortError';
              reject(error);
            }
          },
        };
        effects.push(effect);
        return effect;
      },
    };
  }
  const root = makeNode();
  const state = new CubeState(order);
  const animator = new CubeAnimator(root, state);
  return { root, state, animator, effects };
}

test('disposing a failed renderer removes its old cubies and effects without touching a replacement',async()=>{
  const h=harness(3),task=h.animator.turn(h.state,parseMove('R'),200);
  const rejected=assert.rejects(task,/cancelled/);
  h.effects[0].cancel();await rejected;
  const replacement=new CubeAnimator(h.root,h.state);
  h.animator.dispose();h.animator.dispose();
  assert.equal(h.animator.nodes.size,0);assert.equal(h.animator.layer,null);assert.equal(h.animator.active,null);
  assert.equal(h.root.children.length,26);
  assert.ok(h.root.children.every(n=>replacement.nodes.get(n.dataset.cubieId)===n));
  assert.equal(h.state.isSolved(),true);
});

// The following affine math is independent of the production renderer. CSS
// matrix3d arguments are column-major; all matrices below are row-major.
function multiply4(a, b) {
  return Array.from({ length: 16 }, (_, i) => {
    const row = Math.floor(i / 4), col = i % 4;
    return [0, 1, 2, 3].reduce((sum, k) => sum + a[row * 4 + k] * b[k * 4 + col], 0);
  });
}
function readTransform(value) {
  const match = /^translate3d\(([-.\d]+)px, ([-.\d]+)px, ([-.\d]+)px\) matrix3d\(([^)]+)\)$/.exec(value);
  assert.ok(match, `Expected the renderer's translate × orientation transform, got ${value}`);
  const columnMajor = match[4].split(',').map(Number);
  assert.equal(columnMajor.length, 16);
  const orientation = Array.from({ length: 16 }, (_, i) => columnMajor[(i % 4) * 4 + Math.floor(i / 4)]);
  const translation = [1, 0, 0, +match[1], 0, 1, 0, +match[2], 0, 0, 1, +match[3], 0, 0, 0, 1];
  return multiply4(translation, orientation);
}
function readRotation(value) {
  const match = /^rotate([XYZ])\((-?\d+)deg\)$/.exec(value);
  assert.ok(match, `Expected a single world-axis rotation, got ${value}`);
  const radians = Number(match[2]) * Math.PI / 180;
  const c = Math.cos(radians), s = Math.sin(radians);
  if (match[1] === 'X') return [1, 0, 0, 0, 0, c, -s, 0, 0, s, c, 0, 0, 0, 0, 1];
  if (match[1] === 'Y') return [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1];
  return [c, -s, 0, 0, s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}
function assertMatrixClose(actual, expected, message) {
  actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < 1e-10,
    `${message} at matrix index ${i}: ${value} versus ${expected[i]}`));
}

test('renderer constructs eight cubies with six faces and exactly three external stickers each', () => {
  const { root, state, animator, effects } = harness();
  assert.equal(root.children.length, 8);
  assert.equal(animator.nodes.size, 8);
  assert.equal(effects.length, 0);
  for (const cubie of state.cubies) {
    const node = animator.nodes.get(cubie.id);
    assert.equal(node.parentNode, root);
    assert.equal(node.dataset.cubieId, cubie.id);
    assert.equal(node.children.length, 6);
    assert.equal(node.children.filter(face => face.className.includes(' internal')).length, 3);
    for (const [name, [axis, sign]] of Object.entries(Geometry.faces)) {
      const face = node.children.find(child => child.className.split(' ').includes(name));
      assert.ok(face);
      assert.equal(face.className.includes(' internal'), cubie.position[axis] !== sign);
    }
  }
});

test('geometry and CSS dimensions share one source and maintain equal physical gaps on all three axes', () => {
  const { root, state } = harness();
  assert.equal(Geometry.size, 62);
  assert.equal(Geometry.gap, 4);
  assert.equal(Geometry.radius, 5);
  assert.equal(Geometry.spacing, (Geometry.size + Geometry.gap) / 2);
  assert.equal(root.style['--cube-size'], `${Geometry.size}px`);
  assert.equal(root.style['--cube-half'], `${Geometry.size / 2}px`);
  assert.equal(root.style['--cube-gap'], `${Geometry.gap}px`);
  assert.equal(root.style['--cube-radius'], `${Geometry.radius}px`);
  assert.ok(Geometry.gap / Geometry.size >= .05 && Geometry.gap / Geometry.size <= .08);
  assert.ok(Geometry.radius / Geometry.size >= .08 && Geometry.radius / Geometry.size <= .12);
  for (let axis = 0; axis < 3; axis++) {
    const low = state.cubies.filter(cubie => cubie.position[axis] === -1);
    for (const cubie of low) {
      const opposite = state.cubies.find(candidate => candidate.position[axis] === 1
        && candidate.position.every((value, index) => index === axis || value === cubie.position[index]));
      const a = readTransform(transform(cubie));
      const b = readTransform(transform(opposite));
      assert.equal(b[axis * 4 + 3] - a[axis * 4 + 3] - Geometry.size, Geometry.gap);
    }
  }
});

test('turn reparents exactly one legal face into one shared animation layer without mutating the model', async () => {
  const { root, state, animator, effects } = harness();
  const before = state.snapshot();
  const expected = state.cubies.filter(c => c.position[0] === 1).map(c => c.id).sort();
  const pending = animator.turn(state, parseMove('R'), 420);
  const layer = animator.layer;
  assert.ok(layer);
  assert.equal(layer.className, 'cube-turn-layer');
  assert.equal(layer.parentNode, root);
  assert.equal(root.children.length, 5);
  assert.equal(layer.children.length, 4);
  assert.deepEqual(layer.children.map(node => node.dataset.cubieId).sort(), expected);
  assert.equal(effects.length, 1);
  assert.equal(effects[0].target, layer);
  assert.equal(effects[0].options.duration, Math.round(420 * 1.04));
  assert.equal(effects[0].options.easing, 'cubic-bezier(.4,0,.2,1)');
  assert.equal(effects[0].options.fill, 'forwards');
  assert.deepEqual(effects[0].frames, [{ transform: 'rotateX(0deg)' }, { transform: 'rotateX(90deg)' }]);
  assert.deepEqual(state.snapshot(), before);
  effects[0].finish();
  await pending;
  assert.deepEqual(state.snapshot(), before);
  assert.equal(layer.parentNode, root, 'the final pose is retained until the caller commits');
  assert.equal(effects[0].calls.includes('cancel'), false);
  state.apply('R');
  animator.commit(state);
  assert.equal(root.children.length, 8);
  assert.equal(layer.parentNode, null);
  assert.equal(layer.children.length, 0);
  assert.equal(animator.layer, null);
  assert.equal(animator.layerEffect, null);
  assert.equal(effects[0].calls.at(-1), 'cancel');
  state.cubies.forEach(cubie => {
    const node = animator.nodes.get(cubie.id);
    assert.equal(node.parentNode, root);
    assert.equal(node.dataset.position, cubie.position.join(','));
    assert.equal(node.dataset.orientation, cubie.orientation.join(','));
    assert.equal(node.style.transform, transform(cubie));
  });
});

test('logical row-major orientation becomes a column-major CSS matrix without transposing the rotation', () => {
  const { state, animator } = harness();
  ['F', 'R', "U'", 'B'].forEach(move => state.apply(move));
  animator.render(state);
  for (const cubie of state.cubies) {
    const matrix = readTransform(animator.nodes.get(cubie.id).style.transform);
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) assert.equal(matrix[row * 4 + col], cubie.orientation[row * 3 + col]);
      assert.equal(matrix[row * 4 + 3], cubie.position[row] * Geometry.spacing);
    }
    assert.deepEqual(matrix.slice(12), [0, 0, 0, 1]);
  }
});

for (const order of [2, 3]) {
for (const move of ['R', 'L', 'U', 'D', 'F', 'B'].flatMap(face => [face, face + "'", face + '2'])) {
  test(`${order}×${order} ${move}: final layer × child matrix equals the committed state, including after earlier turns`, async () => {
    const { state, animator, effects, root } = harness(order);
    ['F', 'R', "U'", 'L2', 'B'].forEach(previous => state.apply(previous));
    animator.render(state);
    const before = state.snapshot();
    const pending = animator.turn(state, parseMove(move), 300);
    const layer = animator.layer;
    assert.equal(layer.children.length, order ** 2);
    const finalRotation = readRotation(effects[0].frames.at(-1).transform);
    const expected = new Map();
    for (const cubie of state.cubies) {
      const node = animator.nodes.get(cubie.id);
      const local = readTransform(node.style.transform);
      expected.set(cubie.id, node.parentNode === layer ? multiply4(finalRotation, local) : local);
    }
    assert.deepEqual(state.snapshot(), before);
    effects[0].finish();
    await pending;
    state.apply(move);
    animator.commit(state);
    assert.equal(root.children.length, order === 3 ? 26 : 8);
    for (const cubie of state.cubies) {
      assertMatrixClose(readTransform(animator.nodes.get(cubie.id).style.transform), expected.get(cubie.id), `${move} / ${cubie.id}`);
      assert.ok(cubie.orientation.every(Number.isInteger));
    }
  });
}
}

test('pause and resume keep the same effect and layer and do not alter logical state', async () => {
  const { state, animator, effects } = harness();
  const before = state.snapshot();
  const pending = animator.turn(state, parseMove('F'), 500);
  const effect = animator.active, layer = animator.layer;
  animator.pause();
  assert.equal(animator.paused, true);
  assert.equal(effect.playState, 'paused');
  animator.resume();
  assert.equal(animator.paused, false);
  assert.equal(effect.playState, 'running');
  assert.equal(animator.active, effect);
  assert.equal(animator.layer, layer);
  assert.equal(effects.length, 1);
  assert.deepEqual(effect.calls, ['pause', 'play']);
  assert.deepEqual(state.snapshot(), before);
  effect.finish();
  await pending;
  state.apply('F');
  animator.commit(state);
});

test('a renderer paused before turn immediately pauses its newly created effect', async () => {
  const { state, animator, effects } = harness();
  animator.pause();
  const pending = animator.turn(state, parseMove('U'), 200);
  assert.equal(effects[0].playState, 'paused');
  assert.deepEqual(effects[0].calls, ['pause']);
  animator.resume();
  effects[0].finish();
  await pending;
  state.apply('U');
  animator.commit(state);
});

test('reduced motion finishes the in-flight effect while leaving the exact state commit to the controller', async () => {
  const { root, state, animator, effects } = harness();
  const before = state.snapshot();
  const pending = animator.turn(state, parseMove("L'"), 600);
  animator.pause();
  animator.setReducedMotion(true);
  assert.equal(animator.reduced, true);
  assert.equal(effects[0].playState, 'finished');
  await pending;
  assert.equal(animator.active, null);
  assert.deepEqual(state.snapshot(), before);
  state.apply("L'");
  animator.commit(state);
  assert.equal(root.children.length, 8);
  assert.equal(animator.layer, null);
  animator.resume();
  await animator.turn(state, parseMove('L'), 600);
  assert.equal(effects.length, 1, 'subsequent reduced-motion moves need no new WAAPI effect');
  state.apply('L');
  animator.commit(state);
  assert.equal(state.isSolved(), true);
});

test('zero-duration turns preserve the same legal layer/commit boundary without WAAPI', async () => {
  const { state, animator, effects, root } = harness();
  await animator.turn(state, parseMove('B2'), 0);
  assert.equal(effects.length, 0);
  assert.equal(animator.layer.children.length, 4);
  assert.equal(state.isSolved(), true);
  state.apply('B2');
  animator.commit(state);
  assert.equal(animator.layer, null);
  assert.equal(root.children.length, 8);
  assert.equal(state.isSolved(), false);
});

test('concurrent turns are rejected and cancellation restores nodes without committing a partial rotation', async () => {
  const { state, animator, effects, root } = harness();
  const before = state.snapshot();
  const pending = animator.turn(state, parseMove('R'), 300);
  await assert.rejects(animator.turn(state, parseMove('U'), 300), /already rotating/);
  assert.equal(effects.length, 1);
  const rejection = assert.rejects(pending, { name: 'AbortError' });
  effects[0].cancel();
  await rejection;
  assert.deepEqual(state.snapshot(), before);
  assert.equal(animator.layer, null);
  assert.equal(animator.active, null);
  assert.equal(root.children.length, 8);
  state.cubies.forEach(cubie => assert.equal(animator.nodes.get(cubie.id).style.transform, transform(cubie)));
});

test('hold effects are cancelled after completion without creating a turn layer', async () => {
  const { animator, effects, root } = harness();
  const pending = animator.wait(150);
  assert.equal(effects[0].target, root);
  assert.equal(effects[0].options.duration, 150);
  assert.equal(animator.layer, null);
  effects[0].finish();
  await pending;
  assert.equal(animator.active, null);
  assert.equal(effects[0].calls.at(-1), 'cancel');
});

test('motion cadence is deterministic, keeps exact move endpoints, and preserves the slower final solve baseline', async () => {
  async function durationsFor(moves, baseline) {
    const { animator, state, effects } = harness();
    const durations = [];
    for (const move of moves) {
      const parsed = parseMove(move);
      const pending = animator.turn(state, parsed, baseline);
      const effect = effects.at(-1);
      durations.push(effect.options.duration);
      assert.deepEqual(effect.frames, [
        { transform: `rotate${parsed.axis.toUpperCase()}(0deg)` },
        { transform: `rotate${parsed.axis.toUpperCase()}(${parsed.angle}deg)` },
      ]);
      assert.equal(effect.options.easing, 'cubic-bezier(.4,0,.2,1)');
      effect.finish();
      await pending;
      state.apply(move);
      animator.commit(state);
    }
    return durations;
  }
  const quarters = ['R', "U'", 'F', 'B', "L'", 'D', 'R', 'U'];
  const normal = await durationsFor(quarters, 220);
  const repeat = await durationsFor(quarters, 220);
  const finalSolve = await durationsFor(quarters, 280);
  assert.deepEqual(normal, [229, 216, 220, 211, 229, 216, 220, 211]);
  assert.deepEqual(repeat, normal);
  assert.ok(Math.min(...finalSolve) > Math.max(...normal));
  const halfTurns = await durationsFor(['R2', 'U2', 'F2', 'B2'], 220);
  assert.deepEqual(halfTurns, [275, 259, 264, 253]);
  halfTurns.forEach((duration, index) => assert.ok(duration > normal[index]));
});

test('a hold preserves its requested duration and pause/resume without advancing the turn cadence', async () => {
  const { animator, state, effects } = harness();
  const first = animator.turn(state, parseMove('R'), 220);
  effects[0].finish();
  await first;
  state.apply('R');
  animator.commit(state);
  const holding = animator.wait(375);
  const effect = effects[1];
  assert.equal(effect.options.duration, 375);
  animator.pause();
  assert.equal(effect.playState, 'paused');
  animator.resume();
  assert.equal(animator.active, effect);
  assert.deepEqual(effect.calls, ['pause', 'play']);
  effect.finish();
  await holding;
  const second = animator.turn(state, parseMove('U'), 220);
  assert.equal(effects[2].options.duration, 216);
  effects[2].finish();
  await second;
  state.apply('U');
  animator.commit(state);
});

test('three-tier geometry keeps the same 128px envelope with 26 visible pieces and 54 external stickers', () => {
  const { root, state, animator } = harness(3);
  const geometry = geometryFor(3);
  assert.equal(geometryFor(), Geometry);
  assert.equal(geometryFor(2), Geometry);
  assert.equal(animator.geometry, geometry);
  assert.deepEqual([geometry.size, geometry.gap, geometry.spacing, geometry.radius], [40, 4, 44, 3.3]);
  assert.equal(Object.isFrozen(geometry), true);
  for (const invalid of [0, 1, 4, '3', null, NaN]) assert.throws(() => geometryFor(invalid), RangeError);
  assert.equal(root.children.length, 26);
  assert.equal(root.style['--cube-size'], '40px');
  assert.equal(root.style['--cube-half'], '20px');
  assert.equal(root.style['--cube-gap'], '4px');
  assert.equal(root.style['--cube-radius'], '3.3px');
  let totalStickers = 0;
  const kinds = { 1: 0, 2: 0, 3: 0 };
  for (const cubie of state.cubies) {
    const node = animator.nodes.get(cubie.id);
    assert.equal(node.children.length, 6);
    const exterior = node.children.filter(face => !face.className.includes(' internal')).length;
    assert.equal(exterior, cubie.position.filter(value => value !== 0).length);
    totalStickers += exterior;
    kinds[exterior]++;
    assert.equal(node.style.transform, transform(cubie, geometry));
    for (const [face, [axis, side]] of Object.entries(geometry.faces)) {
      const nodeFace = node.children.find(child => child.className.split(' ').includes(face));
      assert.equal(nodeFace.className.includes(' internal'), cubie.position[axis] !== side);
    }
  }
  assert.equal(totalStickers, 54);
  assert.deepEqual(kinds, { 1: 6, 2: 12, 3: 8 });
  for (let axis = 0; axis < 3; axis++) {
    const centers = state.cubies.map(cubie => readTransform(transform(cubie, geometry))[axis * 4 + 3]);
    assert.equal(Math.max(...centers) - Math.min(...centers) + geometry.size, 128);
    const coordinates = [...new Set(centers)].sort((a, b) => a - b);
    assert.equal(coordinates[1] - coordinates[0] - geometry.size, geometry.gap);
    assert.equal(coordinates[2] - coordinates[1] - geometry.size, geometry.gap);
  }
  assert.equal(2 * Geometry.spacing + Geometry.size, 128);
});

test('three-tier turns animate one nine-piece layer, pause/resume the same effect, and commit only after completion', async () => {
  const { state, animator, effects, root } = harness(3);
  const before = state.snapshot();
  const pending = animator.turn(state, parseMove("U'"), 400);
  const layer = animator.layer, effect = animator.active;
  assert.equal(layer.children.length, 9);
  assert.equal(root.children.length, 18);
  assert.equal(effects.length, 1);
  assert.equal(effect.target, layer);
  assert.deepEqual(state.snapshot(), before);
  animator.pause();
  assert.equal(effect.playState, 'paused');
  animator.resume();
  assert.equal(effect.playState, 'running');
  assert.equal(animator.active, effect);
  assert.equal(animator.layer, layer);
  effect.finish();
  await pending;
  assert.deepEqual(state.snapshot(), before);
  state.apply("U'");
  animator.commit(state);
  assert.equal(root.children.length, 26);
  assert.equal(layer.parentNode, null);
  assert.equal(animator.layer, null);
  assert.equal(animator.active, null);
  animator.setReducedMotion(true);
  await animator.turn(state, parseMove('U'), 400);
  assert.equal(effects.length, 1);
  state.apply('U');
  animator.commit(state);
  assert.deepEqual(state.snapshot(), before);
  assert.equal(state.isSolved(), true);
});
