const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const CubeLogic = require('../../ui/web/static/cube-state.js');
const { Geometry, geometryFor, transform } = require('../../ui/web/static/cube-animator.js');

function node(dataset = {}) {
  return { dataset, style: { setProperty(key, value) { this[key] = value; } }, children: [], attrs: {}, events: {}, open:false, rectReads:0, classList: { add() {}, remove() {} },
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this.attrs[key] = value; },
    getAttribute(key) { return this.attrs[key]; },
    addEventListener(key, fn) {
      const existing = this.events[key];
      this.events[key] = existing ? event => { existing(event); return fn(event); } : fn;
    },
    getBoundingClientRect() { this.rectReads++; return {left:100, top:50, width:320, height:240}; },
    dispatchEvent(event) { this.events[event.type]?.(event); },
    querySelector() { return this.glyph ||= node(); },
    setPointerCapture() {}, focus() { this.focused = true; },
  };
}

// UI tests control the animation boundary; legal moves use the actual state model.
class FakeAnimator {
  constructor(root, state) {
    this.nodes = new Map();
    this.geometry = geometryFor(state.order);
    for (const cubie of state.cubies) {
      const item = node();
      for (let face = 0; face < 6; face++) item.appendChild(node());
      root.appendChild(item);
      this.nodes.set(cubie.id, item);
    }
    this.render(state);
  }
  render(state) {
    for (const cubie of state.cubies) this.nodes.get(cubie.id).style.transform = transform(cubie, this.geometry);
  }
  async turn() {}
  commit(state) { this.render(state); }
  async wait() { await flush(); }
  pause() {}
  resume() {}
  setReducedMotion() {}
}

class FakeLogo {
  constructor({state, animator, onChange, loop}) {
    Object.assign(this, {state, animator, onChange, loop});
    this.stage = 'idle';
    this.history = [];
    this.reasons = new Set();
    this.playCalls = 0;
    this.solveCalls = 0;
    this.reduced = false;
    this.task = null;
  }
  get busy() { return this.task !== null; }
  get paused() { return this.reasons.size > 0; }
  emit(stage = this.stage, progress = {}) {
    this.stage = stage;
    this.onChange({ stage, move:'', index:0, total:0, paused:this.paused,
      solved:this.state.isSolved(), scrambleMoves:[...this.history],
      solveMoves:CubeLogic.inverseSequence(this.history), ...progress });
  }
  play() {
    if (this.reduced) return Promise.resolve();
    if (this.task) return this.task;
    this.playCalls++;
    this.task = new Promise(resolve => { this.finish = resolve; });
    this.emit('intro');
    return this.task;
  }
  commitMove(move) {
    this.state.apply(move);
    this.history.push(move);
    this.animator.render(this.state);
    this.emit('scrambling', {move, index:this.history.length, total:10});
  }
  solve() {
    this.solveCalls++;
    this.resume('user');
    if (!this.task && !this.history.length) { this.emit('idle'); return Promise.resolve(); }
    this.task ||= new Promise(resolve => { this.finish = resolve; });
    this.emit('solving');
    return this.task;
  }
  finishSolve() {
    assert.ok(this.reduced || !this.paused, 'restoration must not be blocked by a stale pause reason');
    for (const move of CubeLogic.inverseSequence(this.history)) this.state.apply(move);
    this.history = [];
    this.animator.render(this.state);
    const finish = this.finish;
    this.task = null;
    this.emit('idle');
    finish?.();
  }
  pause(reason) { this.reasons.add(reason); this.emit(); }
  resume(reason) { this.reasons.delete(reason); this.emit(); }
  setReducedMotion(value) {
    this.reduced = value;
    return value ? this.solve() : (this.task || Promise.resolve());
  }
}

const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(reduced = false, realLogo = false) {
  const ids = {}, timers = new Map();
  const errors=[];
  let seq = 0, language = 'zh';
  const views = ['front','side','top','iso','oblique','bottom'].map(view => node({view}));
  const phases = [0,1,2,3,4,5].map(phase => node({phase: String(phase)}));
  const orders = [2,3].map(order => node({cubeOrder:String(order)}));
  const modes = ['normal','deep'].map(chatMode => node({chatMode}));
  const prompt = node({promptZh:'中文提示', promptEn:'English prompt'});
  const motion = {matches:reduced, events:{}, addEventListener(key, fn) { this.events[key] = fn; }};
  const document = { hidden:false, events:{}, documentElement:node(),
    getElementById(id) { return ids[id] ||= node(); }, createElement: () => node(),
    querySelectorAll(selector) {
      const groups = {'[data-view]':views,'[data-phase]':phases,'[data-prompt-zh]':[prompt],
        '[data-cube-order]':orders,'[data-chat-mode]':modes};
      return selector.split(',').flatMap(group => groups[group.trim()] || []);
    },
    addEventListener(key, fn) { this.events[key] = fn; },
  };
  const context = { document, Event, CubeLogic, EvaBrand:require('../../ui/web/static/logo-tokens.js'), EvaBrandAssets:require('../../ui/web/static/brand-assets.js'),
    CubeRendering:{CubeAnimator:FakeAnimator, Geometry, geometryFor},
    CubeLogoSystem:realLogo?require('../../ui/web/static/cube-logo.js'):{CubeLogo:FakeLogo},
    console:{...console,error(...args){errors.push(args);}}, I18N: { lang: () => language, t: key => key },
    window: { matchMedia: () => motion, addEventListener() {} },
    setTimeout(fn) { timers.set(++seq, fn); return seq; }, clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../ui/web/static/cube-avatar.js'),'utf8') + '\nthis.cube = ParticleController;', context);
  context.cube.init();
  return { cube:context.cube, get logo() { return context.cube._logo; }, ids, views, phases, orders, modes, document, timers, prompt, motion, errors,
    tick() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); },
    english() { language = 'en'; context.cube._translate(); },
    reduce(value) { motion.matches = value; motion.events.change(); },
    async finishSolve(logo = context.cube._logo) { logo.finishSolve(); await flush(); },
  };
}

async function failRealChat(h) {
  let turns=0;
  h.logo.animator.turn=async()=>{if(++turns===3)throw new Error('draw failed');};
  await h.cube._setPhase(5);
  for(let i=0;i<20&&!h.cube._motionFault;i++)await flush();
  assert.ok(h.cube._motionFault);assert.equal(h.logo.stage,'error');
  assert.equal(h.logo.history.length,2);
}

test('chat falls back to the standard mark and retries from its journal before applying the latest order',async()=>{
  const h=harness(false,true);await failRealChat(h);
  const failedLogo=h.logo,original=failedLogo.state,before=original.snapshot(),journal=[...failedLogo.history];
  assert.equal(h.ids.cubeRecovery.hidden,false);assert.equal(h.ids.cubeFallback.hidden,false);
  assert.equal(h.ids.cubeStage.dataset.fallback,'true');assert.equal(h.ids.playEvolution.disabled,true);
  assert.equal(h.ids.chatInput.disabled,undefined,'a logo failure cannot disable the composer');
  h.english();assert.match(h.ids.cubeRecoveryNote.textContent,/conversation can continue/);
  await h.cube.setConversationMode('deep');await h.cube._setPhase(0,2);await h.cube._setPhase(0,3);
  assert.equal(h.cube._order,2);assert.deepEqual(original.snapshot(),before);assert.deepEqual(failedLogo.history,journal);
  const retry=h.cube._retryLogo();assert.equal(h.cube._retryLogo(),retry);assert.equal(h.ids.cubeRetry.disabled,true);
  await retry;
  assert.equal(original.isSolved(),true);assert.deepEqual(failedLogo.history,[]);
  assert.equal(h.cube._order,3);assert.equal(h.cube._phase,0);assert.equal(h.cube._motionFault,null);
  assert.equal(h.ids.cubeRecovery.hidden,true);assert.equal(h.ids.cubeFallback.hidden,true);
  const expected=new CubeLogic.CubeState(3);
  assert.deepEqual(h.logo.state.snapshot(),expected.snapshot());
  assert.deepEqual(Array.from(h.cube._cubies,c=>c.node.style.transform.match(/translate3d\(([^)]+)\)/)[1].split(',').map(parseFloat)),
    expected.cubies.map(c=>c.position.map(value=>value*geometryFor(3).spacing)));
  h.cube._logoError(new Error('late failure'),failedLogo);
  assert.equal(h.cube._motionFault,null,'a retired logo cannot overwrite the replacement');
});

test('chat keeps the static fallback when the journal is inconsistent, including after language and mode changes',async()=>{
  const h=harness(false,true);await failRealChat(h);
  h.logo.state.apply('R');const corrupted=h.logo.state.snapshot(),journal=[...h.logo.history],renderer=h.cube._renderer;
  await h.cube._retryLogo();
  assert.ok(h.cube._motionFault);assert.equal(h.logo.stage,'error');assert.equal(h.cube._renderer,renderer);
  assert.deepEqual(h.logo.state.snapshot(),corrupted);assert.deepEqual(h.logo.history,journal);
  assert.equal(h.ids.cubeRetry.disabled,false);assert.equal(h.ids.cubeRecovery.hidden,false);
  h.cube.setResponding('normal');await h.cube.setConversationMode('deep');h.english();
  assert.equal(h.cube._activeResponseMode,'normal');assert.equal(h.cube.getConversationMode(),'deep');
  assert.match(h.ids.phaseDescription.textContent,/journal is preserved/);
  assert.match(h.ids.cubeModeStatus.textContent,/This reply: 2 × 2/);
});

test('reduced motion during a chat fault preserves the journal until retry and finishes as a static solved mark',async()=>{
  const h=harness(false,true);await failRealChat(h);
  const before=h.logo.state.snapshot(),journal=[...h.logo.history];
  h.reduce(true);await flush();
  assert.deepEqual(h.logo.state.snapshot(),before);assert.deepEqual(h.logo.history,journal);
  await h.cube._retryLogo();
  assert.equal(h.logo.state.isSolved(),true);assert.equal(h.logo._reduced,true);
  assert.equal(h.logo.busy,false);assert.equal(h.cube._motionFault,null);
  assert.equal(h.ids.playEvolution.disabled,true);assert.equal(h.ids.cubeRecovery.hidden,true);
});

test('presentation forms retain distinct module centers and phase five starts the logical loop', () => {
  const {cube, logo, ids} = harness();
  assert.equal(cube._cubies.length, 8);
  assert.equal(ids.cubeRotor.children.length, 8);
  const originalPositions = cube._cubies.map(({node}) => node.style.transform.match(/translate3d\(([^)]+)\)/)[1]);
  for (let phase=0; phase<6; phase++) {
    cube._setPhase(phase);
    const positions = cube._cubies.map(({node}) => node.style.transform.match(/translate3d\(([^)]+)\)/)[1]);
    assert.equal(new Set(positions).size, 8, `form ${phase} must not overlap module centers`);
  }
  assert.equal(logo.playCalls, 1);
  assert.equal(logo.loop, true);
  assert.deepEqual(cube._cubies.map(({node}) => node.style.transform.match(/translate3d\(([^)]+)\)/)[1]), originalPositions,
    'entering the logical loop must preserve the initial presentation spacing');
  assert.equal(logo.state.isSolved(), true);
  assert.equal(ids.cubeMoveStatus.hidden, false);
});

test('layer rotation preserves the lower layer and upper layer distance from the axis', () => {
  const {cube} = harness();
  const original = cube._cubies.map(c => c.node.style.transform);
  cube._setPhase(1);
  cube._cubies.forEach((c, i) => {
    if (c.y > 0) assert.equal(c.node.style.transform, original[i]);
    else {
      const [x,,z] = c.node.style.transform.match(/translate3d\(([^)]+)\)/)[1].split(',').map(parseFloat);
      assert.ok(Math.abs(x*x + z*z - 2*Geometry.spacing*Geometry.spacing) < .001);
    }
  });
});

test('six views are distinct, keyboard rotation clears preset selection, Home restores it', () => {
  const {cube, views, ids} = harness();
  const orientations = new Set();
  views.forEach(v => { v.events.click(); orientations.add(ids.cubeModel.style.transform); assert.equal(v.attrs['aria-pressed'],'true'); });
  assert.equal(orientations.size, 6);
  ids.cubeStage.events.keydown({key:'ArrowRight', preventDefault(){}});
  assert.ok(views.every(v => v.attrs['aria-pressed'] === 'false'));
  ids.cubeStage.events.keydown({key:'Home', preventDefault(){}});
  assert.equal(views[3].attrs['aria-pressed'], 'true');
  assert.equal(cube._angles[0], -24);
});

test('idle mouse hover is bounded presentation motion and leaves the logical cube and camera unchanged', () => {
  const h = harness();
  const stage = h.ids.cubeStage, presence = h.ids.cubePresence;
  const initialState = h.logo.state.snapshot();
  const initialAngles = [...h.cube._angles];
  const initialTransform = h.ids.cubeModel.style.transform;
  const initialModules = Array.from(h.cube._cubies, item => item.node.style.transform);
  stage.events.pointerenter();
  assert.equal(stage.rectReads, 1);
  stage.events.pointermove({pointerType:'mouse', clientX:420, clientY:50});
  assert.equal(parseFloat(presence.style['--hover-x']), 2);
  assert.equal(parseFloat(presence.style['--hover-y']), 3);
  stage.events.pointermove({pointerType:'mouse', clientX:-10000, clientY:10000});
  assert.equal(parseFloat(presence.style['--hover-x']), -2);
  assert.equal(parseFloat(presence.style['--hover-y']), -3);
  assert.equal(stage.rectReads, 1, 'pointer motion uses the bounds cached on entry');
  assert.deepEqual(h.logo.state.snapshot(), initialState);
  assert.deepEqual([...h.cube._angles], initialAngles);
  assert.equal(h.ids.cubeModel.style.transform, initialTransform);
  assert.deepEqual(Array.from(h.cube._cubies, item => item.node.style.transform), initialModules);
  assert.equal(h.logo.playCalls, 0);
  stage.events.pointerleave();
  assert.equal(presence.style['--hover-x'], '0deg');
  assert.equal(presence.style['--hover-y'], '0deg');
  stage.events.pointermove({pointerType:'mouse', clientX:420, clientY:50});
  assert.equal(presence.style['--hover-x'], '0deg');
  assert.equal(presence.style['--hover-y'], '0deg');
  stage.events.pointerenter();
  assert.equal(stage.rectReads, 2, 'a new entry refreshes the bounds');
});

test('hover does not run for touch, presentation forms, or an active logo task', () => {
  const h = harness();
  const stage = h.ids.cubeStage, presence = h.ids.cubePresence;
  const move = pointerType => stage.events.pointermove({pointerType, clientX:420, clientY:50});
  const assertStill = () => {
    assert.equal(presence.style['--hover-x'], '0deg');
    assert.equal(presence.style['--hover-y'], '0deg');
  };
  stage.events.pointerenter();
  move('touch');
  assertStill();
  move('pen');
  assertStill();
  h.phases[2].events.click();
  move('mouse');
  assertStill();
  h.phases[0].events.click();
  h.logo.play();
  move('mouse');
  assertStill();
});

test('reduced motion suppresses hover and clears an existing hover offset immediately', () => {
  const reduced = harness(true);
  reduced.ids.cubeStage.events.pointerenter();
  reduced.ids.cubeStage.events.pointermove({pointerType:'mouse', clientX:420, clientY:50});
  assert.equal(reduced.ids.cubePresence.style['--hover-x'], '0deg');
  assert.equal(reduced.ids.cubePresence.style['--hover-y'], '0deg');
  const h = harness();
  h.ids.cubeStage.events.pointerenter();
  h.ids.cubeStage.events.pointermove({pointerType:'mouse', clientX:420, clientY:50});
  assert.notEqual(parseFloat(h.ids.cubePresence.style['--hover-x']), 0);
  h.reduce(true);
  assert.equal(h.ids.cubePresence.style['--hover-x'], '0deg');
  assert.equal(h.ids.cubePresence.style['--hover-y'], '0deg');
  h.ids.cubeStage.events.pointermove({pointerType:'mouse', clientX:420, clientY:50});
  assert.equal(h.ids.cubePresence.style['--hover-x'], '0deg');
  assert.equal(h.ids.cubePresence.style['--hover-y'], '0deg');
});

test('the inspector is closed by default and toggles debug presentation without changing cube state', () => {
  const h = harness();
  const original = h.logo.state.snapshot();
  assert.equal(h.ids.cubeInspector.open, false);
  assert.equal(h.ids.cubeStage.dataset.debug, 'false');
  h.ids.cubeInspector.open = true;
  h.ids.cubeInspector.events.toggle();
  assert.equal(h.ids.cubeStage.dataset.debug, 'true');
  h.ids.cubeInspector.open = false;
  h.ids.cubeInspector.events.toggle();
  assert.equal(h.ids.cubeStage.dataset.debug, 'false');
  assert.deepEqual(h.logo.state.snapshot(), original);
  assert.equal(h.logo.playCalls, 0);
});

test('settling returns to the hero view without overriding an active drag or ordinary idle view', () => {
  const h = harness();
  h.views[0].events.click();
  h.logo.emit('idle');
  assert.deepEqual([...h.cube._angles], [0,0]);
  h.phases[5].events.click();
  h.ids.cubeStage.events.keydown({key:'ArrowRight', preventDefault(){}});
  const snapshot = h.logo.state.snapshot();
  h.logo.emit('settling');
  assert.deepEqual([...h.cube._angles], [-24,-36]);
  assert.equal(h.ids.cubeModel.style.transform, 'rotateX(-24deg) rotateY(-36deg)');
  assert.deepEqual(h.logo.state.snapshot(), snapshot);
  h.logo.emit('scrambling');
  h.ids.cubeStage.events.pointerdown({button:0, pointerId:1, clientX:0, clientY:0});
  h.ids.cubeStage.events.pointermove({pointerId:1, clientX:20, clientY:10});
  const dragged = [...h.cube._angles];
  h.logo.emit('settling');
  assert.deepEqual([...h.cube._angles], dragged);
  h.ids.cubeStage.events.pointerup();
});

test('playback visits all forms and ends in uninterrupted continuous rotation', () => {
  const h = harness();
  h.ids.playEvolution.events.click();
  for(let phase=1; phase<5; phase++) { h.tick(); assert.equal(h.cube._phase, phase); }
  h.tick();
  assert.equal(h.cube._playing, false);
  assert.equal(h.cube._phase, 5);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.timers.size, 0);
  h.cube._setIdle();
  assert.equal(h.cube._phase, 5);
  assert.equal(h.ids.playLabel.textContent, '暂停旋转');
});

test('replying starts the logical loop and awaits restoration before returning to the selected form', async () => {
  const h = harness();
  h.phases[4].events.click();
  h.cube.setListening();
  assert.equal(h.cube._phase, 1);
  h.cube.setResponding();
  assert.equal(h.cube._state, 'responding');
  assert.equal(h.cube._phase, 5);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.timers.size, 0);
  assert.equal(h.logo.playCalls, 1);
  h.logo.commitMove('R');
  const scrambled = h.logo.state.snapshot();
  h.cube.setListening();
  assert.equal(h.cube._state, 'responding');
  h.cube.burstParticles(2);
  assert.equal(h.cube._phase, 5);
  assert.deepEqual(h.logo.state.snapshot(), scrambled);
  assert.equal(h.logo.playCalls, 1);
  assert.equal(h.timers.size, 0);
  h.document.hidden = true; h.document.events.visibilitychange();
  assert.equal(h.timers.size, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  h.document.hidden = false; h.document.events.visibilitychange();
  assert.equal(h.timers.size, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  h.cube._setIdle();
  assert.equal(h.cube._phase, 5, 'active logical geometry remains until solve completes');
  assert.deepEqual(h.logo.state.snapshot(), scrambled);
  assert.equal(h.logo.solveCalls, 1);
  await h.finishSolve();
  assert.equal(h.cube._phase, 4);
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.ids.cubeStage.dataset.motion, 'off');
  assert.equal(h.ids.cubeMoveStatus.hidden, true);
  assert.equal(h.timers.size, 0);
});

test('logical motion supports pause/resume, preserves independent pause reasons, and resets through solve', async () => {
  const h = harness();
  h.phases[5].events.click();
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.ids.playEvolution.attrs['aria-pressed'], 'true');
  assert.ok(h.views.every(v => v.attrs['aria-pressed'] === 'false'));
  h.ids.playEvolution.events.click();
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  assert.deepEqual([...h.logo.reasons], ['user']);
  assert.equal(h.ids.playEvolution.attrs['aria-pressed'], 'false');
  assert.equal(h.ids.playLabel.textContent, '继续旋转');
  h.document.hidden = true; h.document.events.visibilitychange();
  assert.deepEqual([...h.logo.reasons].sort(), ['hidden', 'user']);
  h.document.hidden = false; h.document.events.visibilitychange();
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  assert.deepEqual([...h.logo.reasons], ['user']);
  h.english();
  assert.equal(h.ids.playLabel.textContent, 'Resume rotation');
  h.ids.playEvolution.events.click();
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.logo.paused, false);
  assert.equal(h.ids.playLabel.textContent, 'Pause rotation');
  h.cube.setListening();
  assert.equal(h.cube._phase, 5);
  h.logo.commitMove('U');
  const resetting = h.ids.resetCube.events.click();
  assert.equal(h.logo.state.isSolved(), false);
  assert.equal(h.cube._phase, 5);
  await h.finishSolve();
  await resetting;
  assert.equal(h.cube._phase, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'off');
  assert.equal(h.ids.playLabel.textContent, 'Play sequence');
  assert.equal(h.views[3].attrs['aria-pressed'], 'true');
  assert.equal(h.timers.size, 0);
});

test('manual form and fixed view choices wait for restoration without overwriting logical state', async () => {
  const h = harness();
  h.cube.setResponding();
  h.logo.commitMove('F');
  let scrambled = h.logo.state.snapshot();
  h.phases[2].events.click();
  assert.equal(h.cube._phase, 5);
  assert.deepEqual(h.logo.state.snapshot(), scrambled);
  await h.finishSolve();
  assert.equal(h.cube._phase, 2);
  assert.equal(h.ids.cubeStage.dataset.motion, 'off');
  h.cube.burstParticles(8);
  h.cube._setIdle();
  assert.equal(h.cube._phase, 2);
  h.phases[5].events.click();
  h.logo.commitMove('R');
  scrambled = h.logo.state.snapshot();
  const viewing = h.views[0].events.click();
  assert.equal(h.cube._phase, 5);
  assert.deepEqual(h.logo.state.snapshot(), scrambled);
  assert.equal(h.views[0].attrs['aria-pressed'], 'false');
  await h.finishSolve();
  await viewing;
  assert.equal(h.cube._phase, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'off');
  assert.equal(h.views[0].attrs['aria-pressed'], 'true');
  assert.equal(h.ids.cubeModel.style.transform, 'rotateX(0deg) rotateY(0deg)');
  h.cube._setIdle();
  assert.equal(h.cube._phase, 0);
  assert.equal(h.timers.size, 0);
});

test('only the latest phase request applies after an in-flight restoration', async () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  h.phases[2].events.click();
  h.phases[4].events.click();
  assert.equal(h.logo.solveCalls, 1, 'rapid choices share the same restoration');
  assert.equal(h.cube._phase, 5);
  await h.finishSolve();
  assert.equal(h.cube._phase, 4);
  assert.equal(h.phases[4].attrs['aria-pressed'], 'true');
  assert.equal(h.phases[2].attrs['aria-pressed'], 'false');
});

test('an old fixed-view request cannot overwrite a newer logical-loop selection', async () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  const oldView = h.views[0].events.click();
  h.phases[5].events.click();
  await h.finishSolve();
  await oldView;
  assert.equal(h.cube._phase, 5);
  assert.equal(h.logo.playCalls, 2);
  assert.ok(h.views.every(view => view.attrs['aria-pressed'] === 'false'));
  assert.equal(h.cube._angles[0], -24);
  assert.equal(h.cube._angles[1], -36);
});

test('a paused loop can finish restoring after a fixed view and visibility change', async () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  h.ids.playEvolution.events.click();
  const viewing = h.views[0].events.click();
  h.document.hidden = true; h.document.events.visibilitychange();
  assert.deepEqual([...h.logo.reasons], ['hidden']);
  h.document.hidden = false; h.document.events.visibilitychange();
  assert.equal(h.logo.paused, false);
  await h.finishSolve();
  await viewing;
  assert.equal(h.cube._phase, 0);
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.views[0].attrs['aria-pressed'], 'true');
});

test('reset restores each presentation form to the original isometric layout', async () => {
  for (const phase of [1,2,3,4]) {
    const h = harness();
    h.phases[phase].events.click();
    h.views[0].events.click();
    await h.ids.resetCube.events.click();
    assert.equal(h.cube._phase, 0);
    assert.equal(h.cube._manualPhase, 0);
    assert.equal(h.views[3].attrs['aria-pressed'], 'true');
  }
});

test('dragging temporarily suspends continuous motion and release paths preserve manual pause', () => {
  for (const release of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    const h = harness();
    h.phases[5].events.click();
    const stage = h.ids.cubeStage;
    stage.events.pointerdown({button:0, pointerId:1, clientX:0, clientY:0});
    assert.equal(stage.dataset.motion, 'paused');
    assert.deepEqual([...h.logo.reasons], ['drag']);
    stage.events.pointermove({pointerId:1, clientX:20, clientY:10});
    assert.equal(h.cube._angles[0], -28.5);
    assert.equal(h.cube._angles[1], -27);
    stage.events[release]();
    assert.equal(stage.dataset.motion, 'running');
    assert.equal(h.logo.paused, false);
    h.ids.playEvolution.events.click();
    stage.events.pointerdown({button:0, pointerId:2, clientX:0, clientY:0});
    stage.events[release]();
    assert.equal(stage.dataset.motion, 'paused');
    assert.deepEqual([...h.logo.reasons], ['user']);
  }
});

test('sequence playback suspends while hidden and a manual form selection cancels its timer', () => {
  const h = harness();
  h.ids.playEvolution.events.click();
  h.tick();
  assert.equal(h.cube._phase, 1);
  h.document.hidden = true; h.document.events.visibilitychange();
  assert.equal(h.timers.size, 0);
  h.document.hidden = false; h.document.events.visibilitychange();
  assert.equal(h.timers.size, 1);
  h.tick();
  assert.equal(h.cube._phase, 2);
  h.phases[5].events.click();
  assert.equal(h.cube._playing, false);
  assert.equal(h.timers.size, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
});

test('reduced motion prevents automatic conversational animation', () => {
  const h = harness(true);
  h.cube.setListening();
  assert.equal(h.cube._phase, 0);
  h.cube.setResponding();
  assert.equal(h.cube._phase, 0);
  assert.equal(h.timers.size, 0);
  h.phases[2].events.click();
  assert.equal(h.cube._phase, 2);
  h.phases[5].events.click();
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  assert.equal(h.ids.playEvolution.disabled, true);
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.timers.size, 0);
});

test('changing the motion preference restores the logo and prevents automatic animation while reduced', async () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  h.reduce(true);
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  assert.equal(h.ids.playEvolution.disabled, true);
  assert.equal(h.logo.solveCalls, 1);
  await h.finishSolve();
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.logo.playCalls, 1);
  assert.match(h.ids.cubeMoveStatus.textContent, /静态标识/);
  h.reduce(false);
  await flush();
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.ids.playEvolution.disabled, false);
  assert.equal(h.logo.playCalls, 2);
  h.phases[0].events.click();
  await h.finishSolve();
  h.ids.playEvolution.events.click();
  assert.equal(h.timers.size, 1);
  h.reduce(true);
  assert.equal(h.cube._playing, false);
  assert.equal(h.timers.size, 0);
});

test('move status follows the controller and translates without changing logical state', () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  const snapshot = h.logo.state.snapshot();
  assert.equal(h.ids.cubeStage.dataset.logoState, 'scrambling');
  assert.equal(h.ids.cubeStage.dataset.solved, 'false');
  assert.equal(h.ids.cubeMoveStatus.textContent, '打乱 · 1/10 · R');
  assert.match(h.ids.cubeMoveStatus.title, /复原: R'/);
  h.english();
  assert.equal(h.ids.cubeMoveStatus.textContent, 'Scrambling · 1/10 · R');
  assert.match(h.ids.cubeMoveStatus.title, /Inverse: R'/);
  assert.deepEqual(h.logo.state.snapshot(), snapshot);
  assert.equal(h.logo.playCalls, 1);
});

test('a rapid reduced-motion toggle resumes only after the interrupted loop restores', async () => {
  const h = harness();
  h.phases[5].events.click();
  h.logo.commitMove('R');
  h.reduce(true);
  h.reduce(false);
  await flush();
  assert.equal(h.logo.playCalls, 1);
  assert.equal(h.logo.state.isSolved(), false);
  await h.finishSolve();
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.logo.playCalls, 2);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.cube._phase, 5);
});

test('conversation and cube-order controls share the same mode and update accessible labels', async () => {
  const h = harness();
  assert.equal(h.cube._order, 2);
  assert.equal(h.logo.state.order, 2);
  assert.equal(h.orders[0].attrs['aria-pressed'], 'true');
  assert.equal(h.modes[0].attrs['aria-pressed'], 'true');
  await h.modes[1].events.click();
  assert.equal(h.cube._order, 3);
  assert.equal(h.logo.state.order, 3);
  assert.equal(h.ids.cubeRotor.children.length, 26);
  assert.equal(h.orders[1].attrs['aria-pressed'], 'true');
  assert.equal(h.modes[1].attrs['aria-pressed'], 'true');
  assert.match(h.ids.cubeStage.attrs['aria-label'], /三阶魔方/);
  assert.match(h.ids.cubeModelLabel.textContent, /3 × 3/);
  assert.match(h.ids.phaseDescription.textContent, /二十六/);
  h.english();
  assert.match(h.ids.cubeStage.attrs['aria-label'], /3 × 3 cube/);
  assert.match(h.ids.cubeModeStatus.textContent, /Deep thinking/);
  await h.orders[0].events.click();
  assert.equal(h.cube._order, 2);
  assert.equal(h.logo.state.order, 2);
  assert.equal(h.ids.cubeRotor.children.length, 8);
  assert.equal(h.orders[0].attrs['aria-pressed'], 'true');
  assert.equal(h.orders[1].attrs['aria-pressed'], 'false');
  assert.equal(h.modes[0].attrs['aria-pressed'], 'true');
  assert.equal(h.modes[1].attrs['aria-pressed'], 'false');
});

test('mode capability failures stay visible without disabling message submission', () => {
  const h = harness();
  h.cube.setModeCapabilities([{mode:'normal', strategy:'native'}, {mode:'deep', strategy:'prompt'}]);
  assert.match(h.ids.chatModeHint.textContent, /所选模式随每条消息发送/);
  h.modes[1].events.click();
  assert.match(h.ids.chatModeHint.textContent, /回答策略/);
  h.cube.setModeCapabilityError();
  assert.match(h.ids.chatModeHint.textContent, /无法读取模型能力/);
  assert.equal(h.modes[1].attrs['aria-pressed'], 'true');
});

test('all three-by-three presentation forms keep 26 distinct finite module centers', async () => {
  const h = harness();
  await h.cube.setConversationMode('deep');
  const originals = Array.from(h.cube._cubies, item => item.node.style.transform);
  const position = item => item.node.style.transform.match(/translate3d\(([^)]+)\)/)[1];
  const originalPositions = Array.from(h.cube._cubies, position);
  for (let phase = 0; phase < 6; phase++) {
    await h.cube._setPhase(phase);
    const positions = Array.from(h.cube._cubies, position);
    assert.equal(positions.length, 26);
    assert.equal(new Set(positions).size, 26, `three-by-three form ${phase} has distinct centers`);
    assert.ok(positions.every(value => value.split(',').every(axis => Number.isFinite(parseFloat(axis)))));
    if (phase === 1) {
      assert.equal(h.cube._cubies.filter(item => item.y < 0).length, 9);
      h.cube._cubies.forEach((item, index) => {
        if (item.y >= 0) assert.equal(item.node.style.transform, originals[index]);
      });
    }
  }
  assert.deepEqual(Array.from(h.cube._cubies, position), originalPositions,
    'three-by-three logical rendering begins at the same centers as the original presentation');
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.logo.playCalls, 1);
});

test('switching the selected mode during a reply queues the next cube without changing the active request', async () => {
  const h = harness();
  h.cube.setResponding();
  const previous = h.logo;
  previous.commitMove('R');
  previous.commitMove('U');
  const scrambled = previous.state.snapshot();
  const originalNode = h.ids.cubeRotor.children[0];
  const switching = h.cube.setConversationMode('deep');
  assert.equal(h.cube.getConversationMode(), 'deep');
  assert.equal(h.cube._requestedOrder, 2);
  assert.equal(h.cube._order, 2);
  assert.equal(h.logo, previous);
  assert.equal(h.ids.cubeRotor.children[0], originalNode);
  assert.equal(h.ids.cubeRotor.children.length, 8);
  assert.deepEqual(previous.state.snapshot(), scrambled);
  assert.equal(previous.solveCalls, 0);
  assert.match(h.ids.chatModeHint.textContent, /下一条消息/);
  assert.equal(h.orders[1].attrs['aria-pressed'], 'true');
  await h.cube._setIdle();
  await h.finishSolve(previous);
  await switching;
  assert.equal(previous.state.isSolved(), true);
  assert.equal(previous.history.length, 0);
  assert.notEqual(h.logo, previous);
  assert.notEqual(h.ids.cubeRotor.children[0], originalNode);
  assert.equal(h.ids.cubeRotor.children.length, 26);
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 0);
  assert.equal(h.cube._state, 'idle');
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.ids.avatarStateLabel.textContent, 'avatar.idle');
  const currentStatus = h.ids.cubeMoveStatus.textContent;
  previous.emit('scrambling', {move:'F', index:1, total:10});
  assert.equal(h.ids.cubeMoveStatus.textContent, currentStatus, 'detached controller events cannot update the new cube');
  h.cube.setResponding();
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 5);
  assert.equal(h.logo.playCalls, 1);
  assert.match(h.ids.avatarStateLabel.textContent, /深度思考/);
});

test('rapid order and form choices share restoration and apply only the latest combination', async () => {
  const h = harness();
  h.phases[5].events.click();
  const previous = h.logo;
  previous.commitMove('F');
  const deep = h.cube.setConversationMode('deep');
  const normal = h.cube.setConversationMode('normal');
  h.phases[2].events.click();
  const latest = h.cube.setConversationMode('deep');
  assert.equal(h.cube._requestedOrder, 3);
  assert.equal(h.cube._requestedPhase, 2);
  assert.equal(h.cube._order, 2);
  assert.equal(previous.solveCalls, 1);
  await h.finishSolve(previous);
  await Promise.all([deep, normal, latest]);
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 2);
  assert.equal(h.ids.cubeRotor.children.length, 26);
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.phases[2].attrs['aria-pressed'], 'true');
  assert.equal(h.orders[1].attrs['aria-pressed'], 'true');
  assert.equal(previous.state.isSolved(), true);
});

test('an order change supersedes a pending view while keeping the latest requested form', async () => {
  const h = harness();
  h.phases[5].events.click();
  const previous = h.logo;
  previous.commitMove('R');
  const viewing = h.views[0].events.click();
  const switching = h.cube.setConversationMode('deep');
  await h.finishSolve(previous);
  await Promise.all([viewing, switching]);
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 0);
  assert.deepEqual([...h.cube._angles], [-24,-36]);
  assert.equal(h.views[0].attrs['aria-pressed'], 'false');
  assert.equal(h.views[3].attrs['aria-pressed'], 'true');
});

test('reply start and completion preserve the chosen order and return to its manual form', async () => {
  for (const [mode, order] of [['normal',2], ['deep',3]]) {
    const h = harness();
    h.phases[4].events.click();
    await h.cube.setConversationMode(mode);
    assert.equal(h.cube._phase, 4);
    h.cube.setResponding();
    assert.equal(h.cube._order, order);
    assert.equal(h.logo.state.order, order);
    assert.equal(h.cube._phase, 5);
    assert.equal(h.logo.playCalls, 1);
    h.logo.commitMove('R');
    h.cube._setIdle();
    await h.finishSolve();
    assert.equal(h.cube._order, order);
    assert.equal(h.cube._requestedOrder, order);
    assert.equal(h.cube._phase, 4);
    assert.equal(h.logo.state.isSolved(), true);
  }
});

test('a reply that ends during an order switch mounts the requested cube without restarting reply motion', async () => {
  const h = harness();
  h.cube.setResponding();
  const previous = h.logo;
  previous.commitMove('R');
  const switching = h.cube.setConversationMode('deep');
  h.cube._setIdle();
  assert.equal(h.cube._requestedOrder, 3);
  assert.equal(h.cube._requestedPhase, 0);
  await h.finishSolve(previous);
  await switching;
  assert.equal(previous.state.isSolved(), true);
  assert.equal(h.cube._state, 'idle');
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 0);
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.ids.cubeStage.dataset.motion, 'off');
});

test('returning to the current order during restoration cancels the replacement and reuses its solved model', async () => {
  const h = harness();
  h.phases[5].events.click();
  const previous = h.logo;
  const originalNode = h.ids.cubeRotor.children[0];
  previous.commitMove('F');
  const deep = h.cube.setConversationMode('deep');
  const normal = h.cube.setConversationMode('normal');
  await h.finishSolve(previous);
  await Promise.all([deep, normal]);
  assert.equal(h.logo, previous);
  assert.equal(h.ids.cubeRotor.children[0], originalNode);
  assert.equal(h.cube._order, 2);
  assert.equal(h.cube._requestedOrder, 2);
  assert.equal(h.cube._phase, 5);
  assert.equal(previous.state.isSolved(), true);
  assert.equal(previous.playCalls, 2);
});

test('switching a paused loop clears its user pause so restoration and the new loop can complete', async () => {
  const h = harness();
  h.phases[5].events.click();
  const previous = h.logo;
  previous.commitMove('U');
  h.ids.playEvolution.events.click();
  assert.equal(previous.paused, true);
  const switching = h.cube.setConversationMode('deep');
  assert.equal(previous.paused, false);
  await h.finishSolve(previous);
  await switching;
  assert.equal(previous.state.isSolved(), true);
  assert.equal(h.cube._order, 3);
  assert.equal(h.logo.paused, false);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
  assert.equal(h.logo.playCalls, 1);
});

test('a hidden order switch retains the old cube until visibility permits its restoration', async () => {
  const h = harness();
  h.phases[5].events.click();
  const previous = h.logo;
  previous.commitMove('F');
  const scrambled = previous.state.snapshot();
  h.document.hidden = true;
  h.document.events.visibilitychange();
  const switching = h.cube.setConversationMode('deep');
  await flush();
  assert.equal(h.cube._order, 2);
  assert.equal(h.logo, previous);
  assert.deepEqual(previous.state.snapshot(), scrambled);
  assert.deepEqual([...previous.reasons], ['hidden']);
  assert.equal(h.ids.cubeStage.dataset.motion, 'paused');
  h.document.hidden = false;
  h.document.events.visibilitychange();
  await h.finishSolve(previous);
  await switching;
  assert.equal(previous.state.isSolved(), true);
  assert.equal(h.cube._order, 3);
  assert.equal(h.logo.paused, false);
  assert.equal(h.ids.cubeStage.dataset.motion, 'running');
});

test('reduced-motion replies switch order as static solved logos without starting a loop', async () => {
  const h = harness(true);
  await h.cube.setConversationMode('deep');
  h.cube.setResponding('deep');
  assert.equal(h.cube._order, 3);
  assert.equal(h.cube._phase, 0);
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.logo.reduced, true);
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.ids.cubeRotor.children.length, 26);
  await h.cube.setConversationMode('normal');
  assert.equal(h.cube._order, 3, 'a mode selected during a response waits for completion');
  h.cube._setIdle();
  assert.equal(h.cube._order, 2);
  assert.equal(h.cube._phase, 0);
  assert.equal(h.logo.state.isSolved(), true);
  assert.equal(h.logo.playCalls, 0);
  assert.equal(h.ids.cubeRotor.children.length, 8);
  assert.equal(h.timers.size, 0);
  h.cube.setResponding('normal');
  assert.equal(h.cube._order, 2);
});

test('suggestions fill the composer without submitting and support language changes', () => {
  const h = harness();
  h.prompt.events.click();
  assert.equal(h.ids.chatInput.value, '中文提示');
  assert.equal(h.ids.chatInput.focused, true);
  h.english(); h.prompt.events.click();
  assert.equal(h.ids.chatInput.value, '中文提示\n\nEnglish prompt');
  assert.equal(h.ids.sendBtn.attrs['aria-label'], 'Send message');
});

test('suggestion clicks preserve the existing draft and do not duplicate the same suggestion or send it',()=>{
  const h=harness();h.ids.chatInput.value='我正在写的想法';
  h.prompt.events.click();h.prompt.events.click();
  assert.equal(h.ids.chatInput.value,'我正在写的想法\n\n中文提示');
  assert.equal(h.ids.promptFeedback.dataset.feedback,'inserted');
  assert.equal(h.cube._state,'idle');assert.equal(h.logo.playCalls,0);
  h.english();assert.match(h.ids.promptFeedback.textContent,/Nothing has been sent/);
});

test('a suggestion exceeding the textarea limit retains the complete draft and gives feedback',()=>{
  const h=harness();h.ids.chatInput.value='保留这份完整草稿';h.ids.chatInput.maxLength=10;
  h.prompt.events.click();assert.equal(h.ids.chatInput.value,'保留这份完整草稿');
  assert.equal(h.ids.promptFeedback.dataset.feedback,'limit');assert.equal(h.ids.chatInput.focused,true);
  assert.match(h.ids.promptFeedback.textContent,/精简草稿/);
});

test('Chinese IME confirmation cannot submit; duplicate submissions are ignored', async () => {
  const ids = {chatForm:node(),chatInput:node(),sendBtn:node(),streamToggle:{checked:true}};
  const context = { Event, document:{getElementById:id=>ids[id]}, ParticleController:{setResponding(){}}, I18N:{t:()=> '发送'} };
  const source = fs.readFileSync(path.join(__dirname,'../../ui/web/static/chat.js'),'utf8');
  vm.runInNewContext(source.slice(0,source.indexOf('// ── Init'))+'\nthis.chat = Chat;',context);
  let submissions=0, requests=0, finish;
  const chat = context.chat;
  chat._addMessage = () => {};
  chat._sendStream = () => { requests++; return new Promise(resolve => {finish=resolve;}); };
  chat._bindForm();
  ids.chatForm.dispatchEvent = () => {submissions++;};
  ids.chatInput.events.keydown({key:'Enter',isComposing:true,preventDefault(){}});
  ids.chatInput.events.keydown({key:'Enter',keyCode:229,preventDefault(){}});
  assert.equal(submissions,0);
  ids.chatInput.events.keydown({key:'Enter',preventDefault(){}});
  assert.equal(submissions,1);
  ids.chatInput.value='第一条';
  const pending = ids.chatForm.events.submit({preventDefault(){}});
  ids.chatInput.value='尚未发送的草稿';
  await ids.chatForm.events.submit({preventDefault(){}});
  assert.equal(requests,1);
  assert.equal(ids.chatInput.value,'尚未发送的草稿');
  finish(); await pending;
  assert.equal(ids.sendBtn.disabled,false);
});
