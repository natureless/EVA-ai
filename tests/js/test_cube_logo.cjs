const assert = require('node:assert/strict');
const { test } = require('node:test');
const { CubeLogo } = require('../../ui/web/static/cube-logo.js');
const { CubeState, parseMove, inverseSequence } = require('../../ui/web/static/cube-state.js');

const flush = () => new Promise(resolve => setImmediate(resolve));
const snapshot = state => JSON.parse(JSON.stringify(state.snapshot()));

// This animator deliberately never mutates CubeState. A turn is a controllable
// promise, allowing assertions while the drawing is only partially complete.
class ControlledAnimator {
  constructor() {
    this.turns = [];
    this.waits = [];
    this.current = null;
    this.paused = false;
    this.reduced = false;
    this.resumeCalls = 0;
    this.commits = [];
  }
  turn(state, move, duration) {
    assert.equal(this.current, null, 'two layer animations must never overlap');
    const turn = { move, duration, before: snapshot(state), finishRequested: false };
    this.turns.push(turn);
    if (this.reduced) return Promise.resolve();
    return new Promise((resolve, reject) => {
      turn.resolve = resolve;
      turn.reject = reject;
      this.current = turn;
    });
  }
  commit(state) { this.commits.push(snapshot(state)); }
  wait(duration) { this.waits.push(duration); return Promise.resolve(); }
  finish() {
    assert.ok(this.current, 'an animation must exist before it can finish');
    this.current.finishRequested = true;
    if (!this.paused || this.reduced) {
      const turn = this.current;
      this.current = null;
      turn.resolve();
    }
  }
  fail(error) {
    assert.ok(this.current);
    const turn = this.current;
    this.current = null;
    turn.reject(error);
  }
  pause() { this.paused = true; }
  resume() {
    this.resumeCalls++;
    this.paused = false;
    if (this.current?.finishRequested) this.finish();
  }
  setReducedMotion(value) {
    this.reduced = value;
    if (value && this.current) this.finish();
  }
}

class InstantAnimator extends ControlledAnimator {
  turn(state, move, duration) {
    assert.equal(this.current, null, 'two layer animations must never overlap');
    const turn = { move, duration, before: snapshot(state) };
    this.current = turn;
    this.turns.push(turn);
    return Promise.resolve().then(() => { this.current = null; });
  }
}

function seededRandom(seed = 12345) {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

function harness(Animator = ControlledAnimator, options = {}) {
  const animator = new Animator();
  const events = [];
  const logo = new CubeLogo({
    animator, loop: false, scrambleLength: 10, rng: seededRandom(),
    onChange: event => events.push(event), ...options,
  });
  return { animator, logo, events, solved: snapshot(logo.state) };
}

async function finishTask(h, task, maxTurns = 250) {
  for (let i = 0; i <= maxTurns; i++) {
    await flush();
    if (!h.logo.busy) return task;
    assert.ok(h.animator.current, 'a busy controlled run should reach its next layer turn');
    h.animator.finish();
  }
  assert.fail('controller did not finish within the bounded number of turns');
}

function assertMoves(actualTurns, expectedMoves) {
  assert.deepEqual(actualTurns.map(turn => turn.move), expectedMoves.map(parseMove));
}

for (const order of [2, 3]) {
test(`${order}×${order}: a cycle commits only completed turns, then restores the exact cubie state by inverse moves`, async () => {
  const state = new CubeState(order);
  const h = harness(ControlledAnimator, { state });
  const task = h.logo.play();
  await flush();
  const scramble = [...h.logo.scrambleMoves];
  assert.equal(scramble.length, 10);
  const expected = new CubeState(order);
  for (let i = 0; i < scramble.length; i++) {
    assert.deepEqual(snapshot(h.logo.state), snapshot(expected), 'in-flight turns cannot change logical state');
    assert.deepEqual(h.logo.history, scramble.slice(0, i));
    assert.deepEqual(h.animator.current.move, parseMove(scramble[i]));
    h.animator.finish();
    expected.apply(scramble[i]);
    await flush();
  }
  const inverses = inverseSequence(scramble);
  for (let i = 0; i < inverses.length; i++) {
    assert.equal(h.logo.stage, 'solving');
    assert.deepEqual(snapshot(h.logo.state), snapshot(expected));
    assert.deepEqual(h.logo.history, scramble.slice(0, scramble.length - i), 'journal entry stays until inverse animation commits');
    assert.deepEqual(h.animator.current.move, parseMove(inverses[i]));
    h.animator.finish();
    expected.apply(inverses[i]);
    await flush();
  }
  await task;
  assert.equal(h.logo.state, state, 'the same real model must survive the full cycle');
  assert.equal(h.logo.state.order, order);
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.equal(h.logo.state.isSolved(), true);
  assert.deepEqual(h.logo.history, []);
  assert.equal(h.logo.stage, 'idle');
  assert.equal(h.logo.busy, false);
  assert.equal(h.logo.completedCycles, 1);
  assert.equal(h.animator.commits.length, 20);
  assert.deepEqual([...new Set(h.events.map(event => event.stage))],
    ['intro', 'scrambling', 'scrambled', 'solving', 'settling', 'idle']);
});
}

test('scramble can stop at a real state and solve uses precisely its recorded inverse', async () => {
  const h = harness();
  await finishTask(h, h.logo.scramble());
  const moves = [...h.logo.history];
  assert.equal(h.logo.stage, 'scrambled');
  assert.equal(moves.length, 10);
  assertMoves(h.animator.turns, moves);
  const expected = new CubeState();
  moves.forEach(move => expected.apply(move));
  assert.deepEqual(snapshot(h.logo.state), snapshot(expected));
  await finishTask(h, h.logo.solve());
  assertMoves(h.animator.turns.slice(10), inverseSequence(moves));
  assert.deepEqual(h.logo.solveMoves, inverseSequence(moves));
  assert.deepEqual(snapshot(h.logo.state), h.solved);
});

test('repeated play and scramble calls share a single running task with no duplicate turns', async () => {
  const h = harness();
  const task = h.logo.play();
  assert.equal(h.logo.play(), task);
  assert.equal(h.logo.scramble(), task);
  await flush();
  assert.equal(h.logo.play({ loop: true }), task);
  assert.equal(h.logo.scramble(), task);
  await finishTask(h, task);
  assert.equal(h.animator.turns.length, 20);
  assert.equal(h.logo.completedCycles, 1);
});

for (const order of [2, 3]) {
test(`${order}×${order}: reset in the middle of a layer finishes it and reverses only committed scramble moves`, async () => {
  const state = new CubeState(order);
  const h = harness(ControlledAnimator, { state });
  const task = h.logo.play({ loop: true });
  await flush();
  const scramble = [...h.logo.scrambleMoves];
  h.animator.finish();
  await flush();
  assert.deepEqual(h.logo.history, scramble.slice(0, 1));
  assert.equal(h.logo.reset(), task);
  assert.equal(h.logo.solve(), task);
  await finishTask(h, task);
  const committed = scramble.slice(0, 2);
  assertMoves(h.animator.turns, [...committed, ...inverseSequence(committed)]);
  assert.deepEqual(h.logo.solveMoves, inverseSequence(committed));
  assert.equal(h.logo.state, state, 'reset must unwind the model instead of replacing it');
  assert.equal(h.logo.state.order, order);
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.deepEqual(h.logo.history, []);
});
}

test('solve before playback starts cancels the scramble without creating a turn', async () => {
  const h = harness();
  const task = h.logo.play({ loop: true });
  assert.equal(h.logo.solve(), task);
  await task;
  assert.equal(h.animator.turns.length, 0);
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.equal(h.logo.stage, 'idle');
});

test('user, hidden, and drag pauses compose without a premature resume or state commit', async () => {
  const h = harness();
  const task = h.logo.play();
  await flush();
  h.logo.pause('user');
  h.logo.pause('hidden');
  h.logo.pause('drag');
  h.logo.pause('hidden');
  h.animator.finish();
  await flush();
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.deepEqual(h.logo.history, []);
  h.logo.resume('drag');
  h.logo.resume('hidden');
  h.logo.resume('unknown');
  assert.equal(h.logo.paused, true);
  assert.equal(h.animator.resumeCalls, 0);
  h.logo.resume('user');
  await flush();
  assert.equal(h.logo.paused, false);
  assert.equal(h.animator.resumeCalls, 1);
  assert.equal(h.logo.history.length, 1);
  await finishTask(h, h.logo.solve());
  await task;
  assert.deepEqual(snapshot(h.logo.state), h.solved);
});

test('reset releases a user pause but respects a still-hidden page', async () => {
  const h = harness();
  const task = h.logo.scramble();
  await flush();
  h.logo.pause('user');
  h.logo.pause('hidden');
  h.animator.finish();
  assert.equal(h.logo.reset(), task);
  await flush();
  assert.equal(h.logo.paused, true);
  assert.equal(h.animator.resumeCalls, 0);
  assert.deepEqual(h.logo.history, []);
  h.logo.resume('hidden');
  await finishTask(h, task);
  assert.equal(h.animator.turns.length, 2);
  assert.deepEqual(snapshot(h.logo.state), h.solved);
});

for (const order of [2, 3]) {
test(`${order}×${order}: enabling reduced motion mid-turn restores through instant inverse moves and disables playback`, async () => {
  const state = new CubeState(order);
  const h = harness(ControlledAnimator, { state });
  const task = h.logo.play({ loop: true });
  await flush();
  const scramble = [...h.logo.scrambleMoves];
  h.animator.finish();
  await flush();
  const reducedTask = h.logo.setReducedMotion(true);
  await reducedTask;
  await task;
  const committed = scramble.slice(0, 2);
  assertMoves(h.animator.turns, [...committed, ...inverseSequence(committed)]);
  assert.ok(h.animator.turns.slice(2).every(turn => turn.duration === 0));
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.equal(h.logo.state, state);
  assert.equal(h.logo.state.order, order);
  assert.equal(h.logo.stage, 'idle');
  const turns = h.animator.turns.length;
  await h.logo.play();
  await h.logo.scramble();
  assert.equal(h.animator.turns.length, turns);
  await h.logo.setReducedMotion(false);
  await finishTask(h, h.logo.play({ loop: false }));
  assert.equal(h.animator.turns.length, turns + 20);
  assertMoves(h.animator.turns.slice(turns, turns + 10), h.logo.scrambleMoves);
  assertMoves(h.animator.turns.slice(turns + 10), inverseSequence(h.logo.scrambleMoves));
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.equal(h.logo.state, state);
});
}

test('100 independent random cycles preserve every cubie position and orientation without drift', async () => {
  const h = harness(InstantAnimator);
  const scrambles = new Set();
  for (let cycle = 0; cycle < 100; cycle++) {
    await h.logo.play();
    scrambles.add(h.logo.scrambleMoves.join(' '));
    assertMoves(h.animator.turns.slice(cycle * 20, cycle * 20 + 10), h.logo.scrambleMoves);
    assertMoves(h.animator.turns.slice(cycle * 20 + 10, cycle * 20 + 20), inverseSequence(h.logo.scrambleMoves));
    assert.deepEqual(snapshot(h.logo.state), h.solved, `exact restoration after cycle ${cycle + 1}`);
    assert.deepEqual(h.logo.history, []);
    assert.equal(h.logo.stage, 'idle');
  }
  assert.equal(h.logo.completedCycles, 100);
  assert.equal(h.animator.turns.length, 2000);
  assert.ok(scrambles.size > 90, 'successive cycles should generate new legal scrambles');
});

test('a failed draw does not commit a partial move and the completed journal remains recoverable', async () => {
  const h = harness();
  const task = h.logo.scramble();
  const rejected = assert.rejects(task, /animation cancelled/);
  await flush();
  const firstMove = h.logo.scrambleMoves[0];
  h.animator.finish();
  await flush();
  const committedState = snapshot(h.logo.state);
  h.animator.fail(new Error('animation cancelled'));
  await rejected;
  assert.equal(h.logo.stage, 'error');
  assert.equal(h.logo.busy, false);
  assert.deepEqual(h.logo.history, [firstMove]);
  assert.deepEqual(snapshot(h.logo.state), committedState);
  await finishTask(h, h.logo.solve());
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assertMoves(h.animator.turns.slice(2), inverseSequence([firstMove]));
});

test('solve requested by the scrambled state callback is honored before the task completes', async () => {
  const animator = new InstantAnimator();
  let solveTask;
  const logo = new CubeLogo({ animator, loop: false, scrambleLength: 10, rng: seededRandom(),
    onChange(status) { if (status.stage === 'scrambled') solveTask = logo.solve(); },
  });
  const solved = snapshot(logo.state);
  const task = logo.scramble();
  await task;
  await solveTask;
  assert.deepEqual(snapshot(logo.state), solved);
  assert.deepEqual(logo.history, []);
  assert.equal(logo.stage, 'idle');
});

test('a render commit failure during scramble retains the completed move in the recovery journal', async () => {
  const h = harness();
  const task = h.logo.scramble();
  const rejected = assert.rejects(task, /render commit failed/);
  await flush();
  const committed = h.logo.scrambleMoves.slice(0, 2);
  h.animator.finish();
  await flush();
  const commit = h.animator.commit.bind(h.animator);
  h.animator.commit = () => { throw new Error('render commit failed'); };
  h.animator.finish();
  await rejected;
  const expected = new CubeState();
  committed.forEach(move => expected.apply(move));
  assert.equal(h.logo.stage, 'error');
  assert.equal(h.logo.busy, false);
  assert.deepEqual(snapshot(h.logo.state), snapshot(expected));
  assert.deepEqual(h.logo.history, committed, 'the completed turn is journaled even when rendering fails');
  h.animator.commit = commit;
  await finishTask(h, h.logo.solve());
  assertMoves(h.animator.turns, [...committed, ...inverseSequence(committed)]);
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.deepEqual(h.logo.history, []);
});

test('a render commit failure during restoration removes the completed inverse before recovery resumes', async () => {
  const h = harness();
  await finishTask(h, h.logo.scramble());
  const scramble = [...h.logo.history];
  const task = h.logo.solve();
  const rejected = assert.rejects(task, /render commit failed/);
  await flush();
  h.animator.finish();
  await flush();
  const commit = h.animator.commit.bind(h.animator);
  h.animator.commit = () => { throw new Error('render commit failed'); };
  h.animator.finish();
  await rejected;
  const remaining = scramble.slice(0, -2);
  const expected = new CubeState();
  remaining.forEach(move => expected.apply(move));
  assert.equal(h.logo.stage, 'error');
  assert.equal(h.logo.busy, false);
  assert.deepEqual(snapshot(h.logo.state), snapshot(expected));
  assert.deepEqual(h.logo.history, remaining, 'the completed inverse cannot be replayed after rendering fails');
  h.animator.commit = commit;
  await finishTask(h, h.logo.solve());
  assertMoves(h.animator.turns, [...scramble, ...inverseSequence(scramble)]);
  assert.deepEqual(h.logo.solveMoves, inverseSequence(remaining));
  assert.deepEqual(snapshot(h.logo.state), h.solved);
  assert.deepEqual(h.logo.history, []);
});

for (const order of [2, 3]) {
test(`${order}×${order}: renderer replacement keeps the authoritative state and restores only committed turns`, async () => {
  const h = harness(ControlledAnimator, {state:new CubeState(order)});
  const original = h.logo.state;
  const failed = h.logo.scramble();
  const rejected = assert.rejects(failed, /draw failed/);
  await flush(); h.animator.finish(); await flush();
  const history = [...h.logo.history], before = snapshot(original);
  h.animator.fail(new Error('draw failed')); await rejected;
  const replacement = new ControlledAnimator();
  let mounted = 0, disposed = 0;
  h.animator.dispose = () => { disposed++; };
  const recovery = h.logo.recover({createAnimator(state) {
    mounted++; assert.equal(state, original); assert.deepEqual(snapshot(state), before);
    return replacement;
  }});
  assert.equal(h.logo.recover(), recovery, 'repeated retry shares the pending recovery');
  assert.equal(h.logo.play(), recovery, 'play cannot overlap recovery');
  assert.equal(h.logo.solve(), recovery, 'solve cannot overlap recovery');
  await finishTask({...h,animator:replacement}, recovery);
  assert.equal(mounted,1); assert.equal(disposed,1); assert.equal(h.logo.state,original);
  assertMoves(replacement.turns,inverseSequence(history));
  assert.deepEqual(snapshot(original),h.solved); assert.deepEqual(h.logo.history,[]);
  assert.equal(h.logo.stage,'idle'); assert.equal(h.logo.busy,false);
});
}

test('recovery rejects a state inconsistent with the journal without mounting or rewriting it', async () => {
  const h=harness();
  const failed=h.logo.scramble(), rejected=assert.rejects(failed,/draw failed/);
  await flush(); h.animator.fail(new Error('draw failed')); await rejected;
  h.logo.state.apply('R');
  const corrupted=snapshot(h.logo.state);let mounted=0;
  await assert.rejects(h.logo.recover({createAnimator(){mounted++;return new InstantAnimator();}}),/does not match/);
  assert.equal(mounted,0);assert.equal(h.logo.stage,'error');assert.equal(h.logo.busy,false);
  assert.deepEqual(snapshot(h.logo.state),corrupted);assert.deepEqual(h.logo.history,[]);
});

test('a failed replacement can be retried without losing the committed journal', async () => {
  const h=harness();
  const failed=h.logo.scramble(), rejected=assert.rejects(failed,/draw failed/);
  await flush();h.animator.finish();await flush();h.animator.fail(new Error('draw failed'));await rejected;
  const before=snapshot(h.logo.state), history=[...h.logo.history];
  await assert.rejects(h.logo.recover({createAnimator(){throw new Error('mount failed');}}),/mount failed/);
  assert.deepEqual(snapshot(h.logo.state),before);assert.deepEqual(h.logo.history,history);
  const replacement=new InstantAnimator();
  await h.logo.recover({createAnimator:()=>replacement});
  assertMoves(replacement.turns,inverseSequence(history));assert.deepEqual(snapshot(h.logo.state),h.solved);
});

test('another inverse draw failure preserves only the remaining moves for the next retry', async () => {
  const h=harness();await finishTask(h,h.logo.scramble());const history=[...h.logo.history];
  const failed=h.logo.solve(), rejected=assert.rejects(failed,/restore failed/);
  await flush();h.animator.fail(new Error('restore failed'));await rejected;
  const replacement=new ControlledAnimator();
  const recovery=h.logo.recover({createAnimator:()=>replacement});
  const rejectedAgain=assert.rejects(recovery,/retry failed/);
  await flush();replacement.finish();await flush();replacement.fail(new Error('retry failed'));await rejectedAgain;
  const remaining=history.slice(0,-1);
  assert.deepEqual(h.logo.history,remaining);
  const final=new InstantAnimator();await h.logo.recover({createAnimator:()=>final});
  assertMoves(final.turns,inverseSequence(remaining));assert.deepEqual(snapshot(h.logo.state),h.solved);
});

test('recovery requested from the error callback waits for the rejected task to release', async () => {
  const animator=new ControlledAnimator(), replacement=new InstantAnimator();let recovery;
  const logo=new CubeLogo({animator,loop:false,onChange(s){
    if(s.stage==='error'&&!recovery)recovery=logo.recover({createAnimator:()=>replacement});
  }});
  const failed=logo.scramble(),rejected=assert.rejects(failed,/draw failed/);
  await flush();animator.finish();await flush();animator.fail(new Error('draw failed'));
  await rejected;await recovery;
  assert.equal(logo.state.isSolved(),true);assert.equal(logo.busy,false);assert.equal(logo.stage,'idle');
});

test('a hidden recovery retains its pause and reduced motion restores without restarting playback', async () => {
  const h=harness();
  const failed=h.logo.scramble(),rejected=assert.rejects(failed,/draw failed/);
  await flush();h.animator.finish();await flush();h.animator.fail(new Error('draw failed'));await rejected;
  const before=snapshot(h.logo.state),replacement=new ControlledAnimator();
  h.logo.pause('hidden');
  const recovery=h.logo.recover({createAnimator:()=>replacement});await flush();
  assert.equal(replacement.paused,true);assert.deepEqual(snapshot(h.logo.state),before);
  await h.logo.setReducedMotion(true);await recovery;
  assert.equal(h.logo.state.isSolved(),true);assert.equal(h.logo.paused,true);
  const count=replacement.turns.length;await h.logo.play();assert.equal(replacement.turns.length,count);
  h.logo.resume('hidden');assert.equal(h.logo.paused,false);
});
