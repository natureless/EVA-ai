const {test}=require('node:test');
const assert=require('node:assert/strict');
const {histogram,install}=require('../../scripts/cube_ui_probe.js');
test('frame histograms count valid intervals, keep exact extrema and exclude invalid measurements',()=>{
  const h=histogram();for(const v of [16,16.5,17,40,Infinity,NaN,-1])h.add(v);
  const s=h.summary();assert.equal(s.count,4);assert.equal(s.meanMs,22.375);assert.equal(s.minMs,16);assert.equal(s.maxMs,40);
  assert.equal(s.over34,1);assert.equal(s.over50,0);assert.deepEqual(s.p50,{lowerMs:16.5,upperMs:16.75});
});

function environment(order=3){
  let time=0,serial=0,observer,exportObserver;
  const timers=new Map(),frames=new Map(),listeners=new Map(),pending=[];
  const workspace={hidden:false},packageButton={getAttribute:()=> 'true'};
  const doc={visibilityState:'visible',querySelector(selector){return ({'#brandOrder':{value:order},'#brandPngSize':{value:2048},'#brandWorkspace':workspace,'#brandExportPackage':packageButton,
    '#brandPackageSave':{hidden:false,getAttribute:()=> 'data:fixture'},'#brandPackageStatus':{textContent:'Generated fixture'}})[selector];},
    querySelectorAll(selector){return Array.from({length:selector==='*'?430:selector==='#brandRotor .cubie'?26:0});},getAnimations:()=>[],
    addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(type){listeners.delete(type);}};
  class Preview {run(value){return value;}}
  class Animator {turn(value){return Promise.resolve(value);}}
  class Logo {_emit(value){return value;}}
  class Canvas {toBlob(fn){fn(null);}}
  class Observer {
    static supportedEntryTypes=['longtask'];
    constructor(fn){this.fn=fn;observer=this;}
    observe(){} disconnect(){this.disconnected=true;}
    takeRecords(){return pending.splice(0);}
  }
  const win={document:doc,EvaBrandStudio:{Preview},CubeRendering:{CubeAnimator:Animator},CubeLogoSystem:{CubeLogo:Logo},HTMLCanvasElement:Canvas,
    URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},MutationObserver:class{constructor(fn){this.fn=fn;exportObserver=this;}observe(){} disconnect(){}},PerformanceObserver:Observer,
    performance:{now:()=>time,memory:{usedJSHeapSize:100, totalJSHeapSize:200}},navigator:{userAgent:'test'},innerWidth:1015,innerHeight:924,devicePixelRatio:1,
    matchMedia:()=>({matches:false}),setTimeout(fn,ms){const id=++serial;timers.set(id,{fn,at:time+ms});return id;},clearTimeout:id=>timers.delete(id),
    requestAnimationFrame(fn){const id=++serial;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id)};
  const logo=new Logo();Object.assign(logo,{state:{order,isSolved:()=>false},completedCycles:1,history:[{face:'R',turns:1}],stage:'scramble',paused:false,busy:true});
  return {win,logo,pending,frames,timers,listeners,workspace,packageButton,get exportObserver(){return exportObserver;},get observer(){return observer;},advance(ms){const end=time+ms;
    for(;;){const entry=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;
      time=entry[1].at;timers.delete(entry[0]);entry[1].fn();}time=end;}};
}

test('fifteen-minute measurements retain first and final samples and freeze the observed end state',()=>{
  const h=environment(),probe=install(h.win);probe.start({order:3,duration:900000});h.logo._emit('unchanged');
  const preview=new h.win.EvaBrandStudio.Preview();preview.logo=h.logo;assert.equal(preview.run('original result'),'original result');
  h.advance(897000);h.logo.completedCycles=22;h.logo.history.push({face:'U',turns:-1});
  h.pending.push({startTime:899000,duration:80});h.advance(3000);
  const end=probe.snapshot();assert.equal(end.formatVersion,2);assert.equal(end.running,false);assert.equal(end.elapsedMs,900000);
  assert.equal(end.samples[0].atMs,0);assert.equal(end.samples.at(-1).atMs,900000);assert.equal(end.samples.at(-1).final,true);
  assert.equal(end.samples.at(-1).cycles,22);assert.ok(end.samples.length<=256);assert.ok(end.sampleCoverage.intervalMs>2500);
  assert.equal(end.longTasks.count,1);assert.equal(end.longTasks.maxMs,80);assert.equal(h.observer.disconnected,true);
  assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);
  h.logo.completedCycles=99;h.logo.history[0].face='F';end.preview.journal[0].face='L';end.samples.length=0;
  assert.equal(probe.snapshot().preview.cycles,22);assert.equal(probe.snapshot().preview.journal[0].face,'R');
  assert.equal(probe.snapshot().samples.at(-1).atMs,900000);assert.throws(()=>probe.start({order:3}));probe.dispose();
});

test('early stop preserves incomplete exports and disposal restores observation hooks',()=>{
  const h=environment(),original=h.win.CubeLogoSystem.CubeLogo.prototype._emit,probe=install(h.win);
  probe.start({order:3,duration:600000});h.logo._emit();h.advance(1000);
  h.listeners.get('click')({target:{closest:()=>({id:'brandExportPng',disabled:false})}});
  h.advance(1000);probe.stop();const stopped=probe.snapshot();
  assert.equal(stopped.samples.at(-1).atMs,2000);assert.equal(stopped.incompleteExports[0].kind,'png');assert.equal(stopped.incompleteExports[0].completed,false);
  assert.equal(stopped.incompleteExports[0].elapsedMs,1000);probe.dispose();probe.dispose();
  assert.equal(h.win.CubeLogoSystem.CubeLogo.prototype._emit,original);assert.equal(h.listeners.size,0);
  assert.equal(h.frames.size,0);assert.equal(h.timers.size,0);assert.throws(()=>probe.start({order:3}));
});

test('overlapping PNG and ZIP exports both complete without inventing raster attribution',()=>{
  const h=environment(),probe=install(h.win);probe.start({order:3});
  const click=id=>h.listeners.get('click')({target:{closest:()=>({id,disabled:false})}});
  click('brandExportPng');h.advance(10);click('brandExportPackage');
  const canvas=new h.win.HTMLCanvasElement();canvas.width=canvas.height=2048;canvas.toBlob(()=>{});
  h.advance(20);const url=h.win.URL.createObjectURL({type:'image/png',size:100});h.win.URL.revokeObjectURL(url);
  h.advance(30);h.packageButton.getAttribute=()=> 'false';h.exportObserver.fn();probe.stop();
  const s=probe.snapshot();assert.deepEqual(s.exports.map(job=>[job.kind,job.elapsedMs,job.completed]),[['png',30,true],['zip',50,true]]);
  assert.equal(s.events.filter(e=>e.type==='overlapping-exports').length,1);assert.equal(s.incompleteExports.length,0);
  assert.ok(s.exports.every(job=>job.rastersAmbiguous&&job.rasters.length===0));
  assert.deepEqual(s.temporaryUrls,{active:0,max:1});probe.dispose();
});
test('overflow percentiles disclose a lower bound instead of claiming a clipped maximum',()=>{
  const h=histogram(1,50);for(const v of [16,20,3000])h.add(v);
  const s=h.summary();assert.deepEqual(s.p95,{atLeastMs:50});assert.equal(s.maxMs,3000);assert.equal(s.over50,1);
  assert.deepEqual(histogram().summary().p99,null);assert.equal(histogram().summary().meanMs,null);
});
