const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { CubeState, parseMove, invertMove, inverseSequence, generateScramble } = require('../../ui/web/static/cube-state.js');

const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const FACES = ['R', 'L', 'U', 'D', 'F', 'B'];
const MOVES = FACES.flatMap(face => [face, face + "'", face + '2']);
const AXIS = { x: 0, y: 1, z: 2 };
function seededRandom(seed) {
  let state = seed >>> 0;
  return () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 4294967296);
}
function applyAll(cube, sequence) { sequence.forEach(move => cube.apply(move)); }
function transform(m, v) { return [0, 1, 2].map(row => m[row * 3] * v[0] + m[row * 3 + 1] * v[1] + m[row * 3 + 2] * v[2]); }
function determinant(m) { return m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]); }
const SOLVED = new CubeState().snapshot();
const SOLVED3 = new CubeState(3).snapshot();

function assertLegal(cube) {
  const solved = cube.order === 3 ? SOLVED3 : SOLVED;
  const count = cube.order === 3 ? 26 : 8;
  assert.equal(cube.cubies.length, count);
  assert.equal(new Set(cube.cubies.map(c => c.id)).size, count);
  assert.equal(new Set(cube.cubies.map(c => c.position.join(','))).size, count);
  for (const cubie of cube.cubies) {
    assert.ok(cubie.position.every(value => value === -1 || value === 1 || (cube.order === 3 && value === 0)));
    assert.ok(cubie.position.some(value => value !== 0), 'the hidden core is not a cubie');
    assert.ok(cubie.position.every(value => !Object.is(value, -0)), 'integer zero has a canonical representation');
    assert.ok(cubie.orientation.every(value => Number.isInteger(value) && Math.abs(value) <= 1));
    assert.equal(determinant(cubie.orientation), 1);
    for (let row = 0; row < 3; row++) {
      for (let other = 0; other < 3; other++) {
        const dot = [0, 1, 2].reduce((sum, k) => sum + cubie.orientation[row * 3 + k] * cubie.orientation[other * 3 + k], 0);
        assert.equal(dot, row === other ? 1 : 0);
      }
    }
    // Local sticker normals stay attached to their original cubie and must
    // point outwards after every turn, including edges and face centers.
    const initial = solved.cubies.find(c => c.id === cubie.id);
    assert.equal(cubie.position.filter(value => value !== 0).length, initial.position.filter(value => value !== 0).length);
    for (let axis = 0; axis < 3; axis++) {
      if (initial.position[axis] === 0) continue;
      const localNormal = [0, 0, 0];
      localNormal[axis] = initial.position[axis];
      const worldNormal = transform(cubie.orientation, localNormal);
      const worldAxis = worldNormal.findIndex(value => value !== 0);
      assert.equal(worldNormal.filter(value => value !== 0).length, 1);
      assert.equal(worldNormal[worldAxis], cubie.position[worldAxis]);
    }
  }
}

test('UMD exposes CubeLogic in a browser without CommonJS', () => {
  const context = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../ui/web/static/cube-state.js'), 'utf8'), context);
  assert.equal(typeof context.CubeLogic.CubeState, 'function');
  assert.equal(new context.CubeLogic.CubeState().isSolved(), true);
});

test('eight cubies initialize solved and snapshots cannot mutate state', () => {
  const cube = new CubeState();
  assertLegal(cube);
  assert.equal(cube.isSolved(), true);
  const snapshot = cube.snapshot();
  snapshot.cubies[0].position[0] = 42;
  snapshot.cubies[0].orientation[0] = 42;
  snapshot.cubies.pop();
  assert.deepEqual(cube.snapshot(), SOLVED);
  const other = new CubeState();
  cube.apply('R');
  assert.deepEqual(other.snapshot(), SOLVED);
});

test('all face conventions match the CSS coordinate basis', () => {
  const definitions = { R: ['x', 1, 1], L: ['x', -1, -1], U: ['y', -1, -1], D: ['y', 1, 1], F: ['z', 1, 1], B: ['z', -1, -1] };
  for (const [face, [axis, layer, turns]] of Object.entries(definitions)) {
    assert.deepEqual(parseMove(face), { face, axis, layer, turns, angle: turns * 90 });
    assert.equal(parseMove(face + "'").turns, -turns);
    assert.equal(parseMove(face + '2').angle, 180);
  }
});

test('outside-view clockwise turns have the expected direct position mappings for every face', () => {
  const cases = [
    ['R', [1, -1, 1], [1, -1, -1]],
    ['L', [-1, -1, 1], [-1, 1, 1]],
    ['U', [1, -1, 1], [-1, -1, 1]],
    ['D', [1, 1, 1], [1, 1, -1]],
    ['F', [1, -1, 1], [1, 1, 1]],
    ['B', [1, -1, -1], [-1, -1, -1]],
  ];
  for (const [move, from, to] of cases) {
    const cube = new CubeState();
    const cubie = cube.cubies.find(c => c.position.every((v, i) => v === from[i]));
    cube.apply(move);
    assert.deepEqual(cubie.position, to, move);
  }
});

for (const move of MOVES) {
  test(`${move} changes exactly its four face cubies and its inverse restores their identities and orientation`, () => {
    const cube = new CubeState();
    const parsed = parseMove(move);
    const affected = cube.apply(move);
    assert.equal(affected.length, 4);
    assert.equal(cube.isSolved(), false);
    for (const initial of SOLVED.cubies) {
      const current = cube.cubies.find(c => c.id === initial.id);
      const shouldMove = initial.position[AXIS[parsed.axis]] === parsed.layer;
      assert.equal(affected.includes(initial.id), shouldMove);
      if (!shouldMove) assert.deepEqual(current, initial);
      else assert.notDeepEqual(current.position, initial.position);
    }
    assertLegal(cube);
    cube.apply(invertMove(move));
    assert.deepEqual(cube.snapshot(), SOLVED);
    assert.equal(cube.isSolved(), true);
  });
}

test('four quarter-turns and two half-turns are identity for every face', () => {
  for (const face of FACES) {
    for (const move of [face, face + "'"]) {
      const cube = new CubeState();
      applyAll(cube, [move, move, move, move]);
      assert.deepEqual(cube.snapshot(), SOLVED);
    }
    const cube = new CubeState();
    applyAll(cube, [face + '2', face + '2']);
    assert.deepEqual(cube.snapshot(), SOLVED);
    const quarter = new CubeState();
    applyAll(quarter, [face, face]);
    cube.apply(face + '2');
    assert.deepEqual(cube.snapshot(), quarter.snapshot());
  }
});

test('opposite faces commute and adjacent faces do not', () => {
  for (const [a, b] of [['R', 'L'], ['U', 'D'], ['F', 'B']]) {
    const left = new CubeState(), right = new CubeState();
    applyAll(left, [a, b]);
    applyAll(right, [b, a]);
    assert.deepEqual(left.snapshot(), right.snapshot());
  }
  const left = new CubeState(), right = new CubeState();
  applyAll(left, ['R', 'U']);
  applyAll(right, ['U', 'R']);
  assert.notDeepEqual(left.snapshot(), right.snapshot());
});

test('orientation composes in world space and cannot be replaced by solved positions', () => {
  const cube = new CubeState();
  const cubie = cube.cubies.find(c => c.position.join(',') === '1,-1,1');
  cube.apply('F');
  cube.apply('R');
  assert.deepEqual(cubie.position, [1, -1, 1]);
  assert.deepEqual(cubie.orientation, [0, -1, 0, 0, 0, -1, 1, 0, 0]);
  assert.notDeepEqual(cubie.orientation, IDENTITY);
  assert.equal(cube.isSolved(), false);
});

test('inverse sequence reverses the order, reverses each move and leaves its input unchanged', () => {
  const sequence = Object.freeze(['R', "U'", 'F2', 'L']);
  assert.deepEqual(inverseSequence(sequence), ["L'", 'F2', 'U', "R'"]);
  assert.deepEqual(inverseSequence(inverseSequence(sequence)), sequence);
  assert.deepEqual(inverseSequence([]), []);
});

test('scramble generation is reproducible, uses legal moves and avoids consecutive axes', () => {
  const sequence = generateScramble(200, seededRandom(1234));
  assert.deepEqual(sequence, generateScramble(200, seededRandom(1234)));
  assert.equal(sequence.length, 200);
  sequence.forEach((move, i) => {
    assert.ok(MOVES.includes(move));
    if (i) assert.notEqual(parseMove(move).axis, parseMove(sequence[i - 1]).axis);
  });
  assert.equal(generateScramble().length, 10);
  assert.deepEqual(generateScramble(0), []);
});

test('invalid moves, sequences, lengths and random sources fail without changing cube state', () => {
  const cube = new CubeState();
  for (const invalid of ['', 'r', 'X', 'Rw', 'R3', "R2'", ' R', 'R ', 'RR', 'R\n', null, undefined, 2, {}, ['R']]) {
    assert.throws(() => parseMove(invalid), TypeError);
    assert.throws(() => invertMove(invalid), TypeError);
    assert.throws(() => cube.apply(invalid), TypeError);
    assert.deepEqual(cube.snapshot(), SOLVED);
  }
  for (const invalid of [null, 'R U', {}, 1, ['R', 'X'], new Array(2)]) assert.throws(() => inverseSequence(invalid), TypeError);
  for (const invalid of [-1, 1.1, NaN, Infinity, '3', null, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => generateScramble(invalid), RangeError);
  assert.throws(() => generateScramble(1, null), TypeError);
  for (const invalid of [-0.1, 1, NaN, Infinity, undefined, '0.5']) assert.throws(() => generateScramble(1, () => invalid), RangeError);
});

test('1,000 deterministic random scrambles remain legal at every step and restore exactly by inverse sequence', () => {
  const rng = seededRandom(20260927);
  for (let i = 0; i < 1000; i++) {
    const cube = new CubeState();
    const sequence = generateScramble(1 + (i % 24), rng);
    for (const move of sequence) { cube.apply(move); assertLegal(cube); }
    for (const move of inverseSequence(sequence)) { cube.apply(move); assertLegal(cube); }
    assert.deepEqual(cube.snapshot(), SOLVED);
    assert.equal(cube.isSolved(), true);
  }
});

test('100 scramble/restore cycles on the same cube have no numerical or identity drift', () => {
  const cube = new CubeState();
  const rng = seededRandom(99);
  const objects = cube.cubies.slice();
  for (let i = 0; i < 100; i++) {
    const sequence = generateScramble(30, rng);
    applyAll(cube, sequence);
    applyAll(cube, inverseSequence(sequence));
    assert.deepEqual(cube.snapshot(), SOLVED);
    assert.equal(cube.isSolved(), true);
    cube.cubies.forEach((cubie, index) => assert.equal(cubie, objects[index]));
  }
});

test('cube order is explicit, supports only 2 or 3, and cannot reinterpret existing pieces', () => {
  assert.equal(new CubeState().order, 2);
  assert.deepEqual(new CubeState(2).snapshot(), SOLVED);
  assert.equal(SOLVED.order, 2);
  assert.equal(SOLVED3.order, 3);
  const cube = new CubeState(3);
  assert.equal(Reflect.set(cube, 'order', 2), false);
  assert.equal(cube.order, 3);
  for (const invalid of [0, 1, 4, -3, 2.5, '3', null, NaN, Infinity]) assert.throws(() => new CubeState(invalid), RangeError);
});

test('three-tier state has 8 corners, 12 edges, 6 face centers, and no hidden core', () => {
  const cube = new CubeState(3);
  assert.equal(cube.cubies.length, 26);
  assertLegal(cube);
  const counts = { 1: 0, 2: 0, 3: 0 };
  cube.cubies.forEach(cubie => counts[cubie.position.filter(value => value !== 0).length]++);
  assert.deepEqual(counts, { 1: 6, 2: 12, 3: 8 });
  const snapshot = cube.snapshot();
  snapshot.cubies[0].position[0] = 99;
  snapshot.cubies[0].orientation[0] = 99;
  snapshot.order = 2;
  assert.deepEqual(cube.snapshot(), SOLVED3);
});

for (const move of MOVES) {
  test(`3×3 ${move} turns exactly nine pieces, preserves the face center position, and inverts exactly`, () => {
    const cube = new CubeState(3);
    const parsed = parseMove(move);
    const axis = AXIS[parsed.axis];
    const expectedIds = cube.cubies.filter(cubie => cubie.position[axis] === parsed.layer).map(cubie => cubie.id);
    const moved = cube.apply(move);
    assert.deepEqual(moved, expectedIds);
    assert.equal(moved.length, 9);
    for (const initial of SOLVED3.cubies) {
      const current = cube.cubies.find(cubie => cubie.id === initial.id);
      if (!moved.includes(initial.id)) assert.deepEqual(current, initial);
      else {
        assert.notDeepEqual(current.orientation, initial.orientation);
        const isCenter = initial.position.filter(value => value !== 0).length === 1;
        if (isCenter) assert.deepEqual(current.position, initial.position);
        else assert.notDeepEqual(current.position, initial.position);
      }
    }
    assertLegal(cube);
    assert.equal(cube.isSolved(), false);
    cube.apply(invertMove(move));
    assert.equal(cube.isSolved(), true);
    assert.deepEqual(cube.snapshot(), SOLVED3);
  });
}

test('3×3 quarter-turn identities and opposite-face commutation preserve full center orientation', () => {
  for (const face of FACES) {
    for (const move of [face, face + "'"]) {
      const cube = new CubeState(3);
      applyAll(cube, [move, move, move, move]);
      assert.deepEqual(cube.snapshot(), SOLVED3);
    }
    const cube = new CubeState(3);
    applyAll(cube, [face + '2', face + '2']);
    assert.deepEqual(cube.snapshot(), SOLVED3);
  }
  for (const [a, b] of [['R', 'L'], ['U', 'D'], ['F', 'B']]) {
    const first = new CubeState(3), second = new CubeState(3);
    applyAll(first, [a, b]);
    applyAll(second, [b, a]);
    assert.deepEqual(first.snapshot(), second.snapshot());
  }
});

test('3×3 face-center identities stay fixed and 100 full scramble/inverse cycles have no drift', () => {
  const cube = new CubeState(3);
  const rng = seededRandom(20261003);
  const references = cube.cubies.slice();
  const centers = SOLVED3.cubies.filter(cubie => cubie.position.filter(value => value !== 0).length === 1);
  for (let cycle = 0; cycle < 100; cycle++) {
    const sequence = generateScramble(30, rng);
    for (const move of [...sequence, ...inverseSequence(sequence)]) {
      cube.apply(move);
      assertLegal(cube);
      for (const initial of centers) assert.deepEqual(cube.cubies.find(cubie => cubie.id === initial.id).position, initial.position);
    }
    assert.deepEqual(cube.snapshot(), SOLVED3);
    assert.equal(cube.isSolved(), true);
    cube.cubies.forEach((cubie, index) => assert.equal(cubie, references[index]));
  }
});
