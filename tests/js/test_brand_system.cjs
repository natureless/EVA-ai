const assert = require('node:assert/strict');
const { test } = require('node:test');
const EvaBrand = require('../../ui/web/static/logo-tokens.js');
globalThis.EvaBrand = EvaBrand;
const CubeRendering = require('../../ui/web/static/cube-animator.js');

test('brand tokens define one master envelope for both cube orders', () => {
  const { geometry } = EvaBrand.tokens;
  for (const order of [2, 3]) {
    const g = EvaBrand.geometry(order);
    assert.equal(2 * g.spacing + g.cubieSize, geometry.envelope);
    assert.equal(CubeRendering.geometryFor(order).size, g.cubieSize);
    assert.equal(CubeRendering.geometryFor(order).gap, g.gap);
    assert.equal(CubeRendering.geometryFor(order).radius, g.radius);
  }
  assert.deepEqual([...geometry.heroAngle], [-24, -36]);
  assert.equal(EvaBrand.camera('iso'),geometry.heroAngle);
  assert.ok(geometry.perspective > 0);
});

test('brand token CSS application exposes only the approved design variables', () => {
  const values = new Map();
  EvaBrand.applyCss({style: {setProperty(key, value) { values.set(key, value); }}}, 'light');
  assert.equal(values.get('--brand-cube-size'), '62px');
  assert.equal(values.get('--brand-cube-gap'), '4px');
  assert.equal(values.get('--brand-cube-radius'), '5px');
  assert.equal(values.get('--brand-perspective'), '1100px');
  assert.equal(values.get('--brand-cube-white'), '#f8f8f6');
});

const assets=require('../../ui/web/static/brand-assets.js');
const {Preview,preferences,readPreferences,writePreferences,defaults}=require('../../ui/web/static/brand-system.js');
const logic=require('../../ui/web/static/cube-state.js');
const {KitDelivery}=require('../../ui/web/static/brand-system.js');
const fs=require('node:fs');
const path=require('node:path');
function immediateAnimator(){return {turn:async()=>{},commit(){},wait:async()=>{},pause(){},resume(){},setReducedMotion(){}};}

test('a delivery captures click-time selection while the preview changes during generation',()=>{
  const delivery=new KitDelivery(),selection={order:3,variant:'dark',view:'front',size:2048};
  const snapshot=delivery.start(selection);
  selection.view='bottom';selection.size=512;
  const ready=delivery.finish({href:'data:application/zip;base64,AAAA',name:'original.zip'});
  assert.equal(ready.selection,snapshot);
  assert.deepEqual(ready.selection,{order:3,variant:'dark',view:'front',size:2048});
  assert.equal(delivery.matches(selection),false);
  assert.equal(delivery.matches({...selection,view:'front',size:2048,static:true,reduced:true}),true);
  assert.equal(ready.href,'data:application/zip;base64,AAAA');
});

test('a failed replacement keeps the previous kit available and a successful retry replaces it',()=>{
  const delivery=new KitDelivery(),first={order:2,variant:'primary',view:'iso',size:1024},next={...first,order:3};
  delivery.start(first);const previous=delivery.finish({href:'first',name:'first.zip'});
  delivery.start(next);assert.throws(()=>delivery.start(first),/already/);
  delivery.fail();assert.equal(delivery.ready,previous);assert.equal(delivery.pending,null);
  assert.equal(delivery.matches(first),true);assert.equal(delivery.matches(next),false);
  delivery.start(next);delivery.finish({href:'next',name:'next.zip'});
  assert.equal(delivery.matches(next),true);assert.equal(delivery.ready.href,'next');
});

test('every approved asset exports finite external faces with canonical clear space',()=>{
  for(const variant of EvaBrand.variants)for(const order of [2,3]){
    const xml=assets.svg({order,variant,size:256});
    assert.equal((xml.match(/<path /g)||[]).length,3*(variant==='favicon'?2:order)**2);
    assert.equal(/NaN|Infinity|undefined/.test(xml),false);
    for(const pair of xml.matchAll(/([\d.]+),([\d.]+)/g)){
      assert.ok(Number(pair[1])>=16&&Number(pair[1])<=84);
      assert.ok(Number(pair[2])>=16&&Number(pair[2])<=84);
    }
  }
  assert.throws(()=>assets.svg({variant:'invalid'}),RangeError);
  assert.throws(()=>assets.svg({size:1}),RangeError);
});

test('generated navigation and favicon assets match current canonical geometry',()=>{
  for(const [name,options] of [['eva-mark.svg',{order:2,variant:'primary',size:256}],['eva-favicon.svg',{order:2,variant:'favicon',size:32}]]){
    assert.equal(fs.readFileSync(path.join(__dirname,'../../ui/web/static',name),'utf8'),assets.svg(options)+'\n');
  }
  assert.match(assets.svg(),/ Q/);
  assert.doesNotMatch(assets.svg({variant:'favicon'}),/ Q/);
  assert.equal(EvaBrand.palette('favicon').edge,'#1d1d1f');
  assert.equal(assets.svg({variant:'favicon',order:3,view:'bottom'}),assets.svg({variant:'favicon',order:2,view:'iso'}));
});

test('studio scramble and solve use the exact reverse journal for both orders',async()=>{
  for(const order of [2,3]){
    const turns=[];
    const p=new Preview({mount:()=>({...immediateAnimator(),turn:async(_,move)=>turns.push(move)})});
    await p.selectOrder(order);await p.run('scramble');
    const journal=[...p.logo.history];assert.equal(journal.length,order===2?10:14);
    assert.equal(p.state.isSolved(),false);
    turns.length=0;await p.run('solve');
    // State restoration and empty journal also verify orientation, not just positions.
    assert.equal(p.state.isSolved(),true);assert.equal(p.logo.history.length,0);
    assert.deepEqual(turns,logic.inverseSequence(journal).map(logic.parseMove));
  }
});

test('rapid order requests restore the old state and apply only the last structure',async()=>{
  const mounted=[],p=new Preview({mount:state=>{mounted.push(state);return immediateAnimator();}});
  await p.run('scramble');const previous=p.state;
  await Promise.all([p.selectOrder(3),p.selectOrder(2),p.selectOrder(3)]);
  assert.equal(previous.isSolved(),true);assert.equal(p.order,3);assert.equal(mounted.length,2);
});

test('reduced motion restores an existing scramble and keeps subsequent previews static',async()=>{
  const p=new Preview({mount:immediateAnimator});await p.run('scramble');
  await p.setReduced(true);assert.equal(p.state.isSolved(),true);
  await p.run('scramble');assert.equal(p.logo.history.length,0);
  await p.selectOrder(3);assert.equal(p.state.isSolved(),true);assert.equal(p.logo._reduced,true);
});

test('all six canonical camera views export only visible external faces in both orders',()=>{
  for(const order of [2,3])for(const view of Object.keys(EvaBrand.tokens.geometry.views)){
    const xml=assets.svg({order,view});
    assert.equal((xml.match(/<path /g)||[]).length,order**2*(['iso','oblique'].includes(view)?3:1));
    assert.equal(/NaN|Infinity|undefined/.test(xml),false);
    for(const pair of xml.matchAll(/([\d.]+),([\d.]+)/g)){
      assert.ok(Number(pair[1])>=16&&Number(pair[1])<=84);
      assert.ok(Number(pair[2])>=16&&Number(pair[2])<=84);
    }
  }
  assert.match(assets.svg({view:'bottom'}),new RegExp(EvaBrand.palette().bottom));
  assert.throws(()=>assets.svg({view:'arbitrary'}),RangeError);
  assert.throws(()=>EvaBrand.camera('__proto__'),RangeError);
});

test('preferences restore validated display options and ignore injected geometry or motion state',()=>{
  const requested={variant:'dark',order:3,view:'bottom',static:true,reduced:true,pngSize:2048};
  const normalized=preferences({...requested,gap:99,history:['R'],version:22});
  assert.deepEqual(normalized,{...defaults,...requested});
  assert.deepEqual(preferences({variant:'bad',order:7,view:'bad',static:'true',reduced:1,pngSize:-1}),defaults);
  let content=null;
  const storage={getItem:()=>content,setItem:(_,value)=>content=value};
  assert.equal(writePreferences(storage,normalized),true);
  assert.deepEqual(readPreferences(storage),normalized);
  content='{broken';assert.deepEqual(readPreferences(storage),defaults);
  assert.deepEqual(readPreferences({getItem:key=>key==='eva-brand-variant'?'monochrome':null}),{...defaults,variant:'monochrome'});
  const blocked={getItem(){throw new Error('disabled');},setItem(){throw new Error('disabled');}};
  assert.deepEqual(readPreferences(blocked),defaults);assert.equal(writePreferences(blocked,requested),false);
});

test('interrupting continuous preview finishes its layer and reverses it before replacing geometry',async()=>{
  let release,first=true;
  const p=new Preview({mount:()=>({...immediateAnimator(),turn:async()=>{
    if(first){first=false;await new Promise(resolve=>release=resolve);}
  }})});
  const previous=p.state,previousLogo=p.logo;
  const playing=p.run('continuous');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(previousLogo._loop,true);assert.equal(previousLogo.history.length,0);
  const changing=p.selectOrder(3);
  assert.equal(p.order,2);release();await Promise.all([playing,changing]);
  assert.equal(previous.isSolved(),true);assert.equal(previousLogo.history.length,0);
  assert.equal(p.order,3);assert.equal(p.state.isSolved(),true);
});

test('user pause in continuous preview cannot release an independent hidden-page pause',async()=>{
  let p,verified=false;
  p=new Preview({mount:immediateAnimator,onChange:s=>{
    if(s.stage!=='scrambled'||verified)return;
    verified=true;const snapshot=p.state.snapshot();
    p.togglePause();assert.equal(p.userPaused,true);assert.equal(p.logo.paused,true);
    p.setHidden(true);p.togglePause();assert.equal(p.userPaused,false);assert.equal(p.logo.paused,true);
    assert.deepEqual(p.state.snapshot(),snapshot);
    p.setHidden(false);assert.equal(p.logo.paused,false);p.logo.solve();
  }});
  await p.run('continuous');assert.equal(verified,true);assert.equal(p.state.isSolved(),true);
});

for(const order of [2,3])test(`${order}×${order}: a failed studio preview keeps its model and retries with a new renderer`,async()=>{
  let turns=0,mounted=0;const inverse=[];
  const p=new Preview({order,mount:state=>{
    mounted++;
    if(mounted===1)return {...immediateAnimator(),turn:async()=>{if(++turns===4)throw new Error('draw failed');}};
    assert.equal(state,p.state);
    return {...immediateAnimator(),turn:async(_,move)=>inverse.push(move)};
  }});
  const original=p.state;
  await assert.rejects(p.run('scramble'),/draw failed/);
  const journal=[...p.logo.history];assert.equal(journal.length,3);assert.equal(p.failed,true);
  await assert.rejects(p.selectOrder(order===2?3:2),/Retry/);
  await assert.rejects(p.run('continuous'),/Retry/);
  const retry=p.recover();assert.equal(p.recover(),retry);
  await retry;
  assert.equal(p.failed,false);assert.equal(p.recovering,null);assert.equal(p.state,original);
  assert.equal(p.logo.animator,p.animator);assert.equal(original.isSolved(),true);
  assert.deepEqual(inverse,logic.inverseSequence(journal).map(logic.parseMove));assert.equal(mounted,2);
});

test('studio retry preserves failure and journal when reconstruction fails, then recovers under reduced motion',async()=>{
  let turns=0,broken=false;
  const p=new Preview({mount:()=>{
    if(broken)throw new Error('mount failed');
    return {...immediateAnimator(),turn:async()=>{if(++turns===3)throw new Error('draw failed');}};
  }});
  await assert.rejects(p.run('scramble'),/draw failed/);
  const original=p.state,before=original.snapshot(),journal=[...p.logo.history];
  broken=true;await assert.rejects(p.recover(),/mount failed/);
  assert.equal(p.failed,true);assert.equal(p.recovering,null);
  assert.deepEqual(original.snapshot(),before);assert.deepEqual(p.logo.history,journal);
  p.setHidden(true);await p.setReduced(true);broken=false;await p.recover();
  assert.equal(p.failed,false);assert.equal(original.isSolved(),true);assert.equal(p.logo.paused,true);
  assert.equal(p.logo._reduced,true);await p.run('continuous');assert.deepEqual(p.logo.history,[]);
});
