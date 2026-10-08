const {test}=require('node:test');
const assert=require('node:assert/strict');
const config=require('../../ui/web/static/brand-config.js');
const {Preview}=require('../../ui/web/static/brand-system.js');
const logic=require('../../ui/web/static/cube-state.js');
const brand=require('../../ui/web/static/logo-tokens.js');
const changed={...config.defaults,variant:'dark',order:3,view:'bottom',static:true,reduced:true,pngSize:256};
const draft=()=>JSON.parse(config.serialize(changed));
const rejects=(input,code)=>assert.throws(()=>config.parse(typeof input==='string'?input:JSON.stringify(input)),e=>e.code===code);
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function animator(turns=[]){return {turn:async(_,move)=>turns.push(move),commit(){},wait:async()=>{},pause(){},resume(){},setReducedMotion(){}};}

test('display JSON round-trips all six approved preferences without geometry, journal or service settings',()=>{
  const parsed=config.parse(config.serialize(changed));assert.deepEqual(parsed,{value:changed,migrated:false});
  const output=draft();assert.deepEqual(Object.keys(output.display),config.fields);
  assert.equal(output.format,'eva-brand-display');assert.equal(output.formatVersion,1);
  assert.equal(output.brandVersion,'1.0.0');assert.equal(output.tokenSchemaVersion,1);
  assert.ok(Object.isFrozen(parsed.value));
});
test('only the explicit complete local v1 preference shape migrates',()=>{
  assert.deepEqual(config.parse(JSON.stringify(changed)),{value:changed,migrated:true});
  rejects({...changed,version:2},'unknown-version');
  const incomplete={...changed};delete incomplete.view;rejects(incomplete,'missing-fields');
  rejects({...changed,geometry:{gap:99}},'unexpected-fields');
});
test('unknown contracts and metadata versions are rejected before any preference normalization',()=>{
  for(const patch of [{formatVersion:2},{tokenSchemaVersion:2},{brandVersion:'1.0.1'},{brandVersion:'2.0.0'}])rejects({...draft(),...patch},'unknown-version');
  rejects({...draft(),format:'brand-kit'},'unknown-format');rejects(null,'invalid-values');rejects([],'invalid-values');rejects('{}','missing-fields');
});
test('unapproved root and display fields cannot import geometry, scripts, chat mode or credentials',()=>{
  for(const key of ['geometry','css','script','history','mode','runtime','apiKey','__proto__']){
    const input=draft();Object.defineProperty(input.display,key,{value:'untrusted',enumerable:true});rejects(input,'unexpected-fields');
    rejects({...draft(),[key]:'untrusted'},'unexpected-fields');
  }
});
test('invalid types, unsupported cameras and PNG sizes do not silently default to valid settings',()=>{
  for(const patch of [{order:'3'},{order:4},{variant:'neon'},{view:'constructor'},{view:'__proto__'},{static:1},{reduced:'false'},{pngSize:17},{pngSize:4096}]){
    const input=draft();input.display={...input.display,...patch};rejects(input,'invalid-values');
  }
  const input=draft();delete input.display.pngSize;rejects(input,'missing-fields');
});
test('malformed and oversized UTF-8 documents are refused with bounded, distinct error codes',()=>{
  rejects('{oops','invalid-json');rejects(' '.repeat(config.maxBytes+1),'too-large');
  rejects('中'.repeat(config.maxBytes/2),'too-large');
});
test('review is read-only and invalid apply leaves the current and undo snapshots untouched',async()=>{
  const controller=new config.Controller();await controller.apply(changed);
  const state=controller.active,previous=controller.previous,revision=controller.revision;
  const reviewed=config.parse(config.serialize(config.defaults));
  assert.equal(config.differences(state,reviewed.value).length,6);assert.equal(controller.active,state);
  await assert.rejects(controller.apply({...changed,pngSize:4096}),e=>e.code==='invalid-values');
  assert.equal(controller.active,state);assert.equal(controller.previous,previous);assert.equal(controller.revision,revision);assert.equal(controller.pending,0);
});
test('rapid valid requests commit only the last selection and never persist a superseded result',async()=>{
  const waits=[],commits=[];
  const controller=new config.Controller({prepare:()=>{const gate=deferred();waits.push(gate);return gate.promise;},commit:value=>commits.push(value)});
  const first=controller.apply({...changed,order:2}),last=controller.apply(changed);
  waits[1].resolve(true);assert.equal(await last,true);waits[0].resolve(true);assert.equal(await first,false);
  assert.deepEqual(commits,[changed]);assert.deepEqual(controller.active,changed);assert.deepEqual(controller.previous,config.defaults);assert.equal(controller.pending,0);
});
test('latest rejected or failed preparation restores the controls and retains a usable undo point',async()=>{
  let result=true;const controller=new config.Controller({prepare:async()=>{if(result instanceof Error)throw result;return result;}});
  await controller.apply(changed);const previous=controller.previous;
  result=false;assert.equal(await controller.apply(config.defaults),false);assert.deepEqual(controller.desired,changed);
  result=new Error('renderer failed');await assert.rejects(controller.undo(),/renderer failed/);
  assert.deepEqual(controller.active,changed);assert.equal(controller.previous,previous);assert.equal(controller.pending,0);
  result=true;assert.equal(await controller.undo(),true);assert.deepEqual(controller.active,config.defaults);assert.equal(controller.previous,null);
});
test('camera and PNG choices during restoration stay current without starting another geometry transaction',async()=>{
  const gate=deferred(),commits=[];let preparations=0;
  const controller=new config.Controller({prepare:()=>{preparations++;return gate.promise;},commit:v=>commits.push(v)});
  const request=controller.apply(changed);controller.present({view:'front',pngSize:512});
  assert.equal(preparations,1);assert.equal(commits.length,0);gate.resolve(true);await request;
  assert.equal(controller.active.view,'front');assert.equal(controller.active.pngSize,512);assert.equal(controller.active.order,3);assert.equal(commits.length,1);
  assert.throws(()=>controller.present({gap:99}),e=>e.code==='unexpected-fields');
});
test('unavailable persistent storage does not roll back a valid preview or remove its undo point',async()=>{
  const controller=new config.Controller({commit:()=>{throw new Error('storage blocked');}});
  assert.equal(await controller.apply(changed),true);assert.deepEqual(controller.active,changed);assert.equal(controller.saved,false);
  assert.equal(await controller.undo(),true);assert.deepEqual(controller.active,config.defaults);assert.equal(controller.saved,false);
});
test('presentation changes made during a failed restoration persist only after that transaction ends',async()=>{
  const gate=deferred(),commits=[];
  const controller=new config.Controller({prepare:()=>gate.promise,commit:value=>commits.push(value)});
  const request=controller.apply(changed);controller.present({view:'front',pngSize:512});
  assert.equal(commits.length,0);gate.reject(new Error('renderer failed'));
  await assert.rejects(request,/renderer failed/);
  const expected={...config.defaults,view:'front',pngSize:512};
  assert.deepEqual(controller.active,expected);assert.deepEqual(controller.desired,expected);
  assert.deepEqual(commits,[expected]);assert.equal(controller.pending,0);
  assert.equal(controller.previous.view,'iso');assert.equal(controller.previous.pngSize,1024);
});
test('apply and undo restore actual committed moves before replacing either cube order',async()=>{
  const turns=[],oldModels=[];const preview=new Preview({mount:state=>{oldModels.push(state);return animator(turns);}});
  const controller=new config.Controller({prepare:(value,current)=>config.preparePreview(preview,value,current)});
  await preview.run('scramble');const firstState=preview.state,firstJournal=[...preview.logo.history];turns.length=0;
  await controller.apply({...changed,static:false,reduced:false});
  assert.equal(firstState.isSolved(),true);assert.deepEqual(turns,logic.inverseSequence(firstJournal).map(logic.parseMove));assert.equal(preview.order,3);assert.equal(preview.logo.history.length,0);
  await preview.run('scramble');const secondState=preview.state,secondJournal=[...preview.logo.history];turns.length=0;
  await controller.undo();assert.equal(secondState.isSolved(),true);assert.deepEqual(turns,logic.inverseSequence(secondJournal).map(logic.parseMove));assert.equal(preview.order,2);assert.equal(preview.logo.history.length,0);assert.equal(oldModels.length,3);
});
test('system reduced motion wins over an imported false preference and keeps both orders static',async()=>{
  const preview=new Preview({mount:()=>animator()});await preview.run('scramble');const before=preview.state;
  const controller=new config.Controller({prepare:(value,current)=>config.preparePreview(preview,value,current,true)});
  await controller.apply({...changed,reduced:false,static:false});
  assert.equal(before.isSolved(),true);assert.equal(preview.reduced,true);assert.equal(preview.logo._reduced,true);assert.equal(controller.active.reduced,false);
  await preview.run('continuous');assert.equal(preview.logo.history.length,0);await controller.undo();assert.equal(preview.reduced,true);assert.equal(preview.logo._reduced,true);
});
test('failed renderer preserves the journal and settings until explicit recovery enables configuration',async()=>{
  let fail=true,count=0;
  const preview=new Preview({mount:()=>({...animator(),turn:async()=>{if(fail&&++count===2)throw new Error('layer failed');}})});
  await assert.rejects(preview.run('scramble'),/layer failed/);const state=preview.state,journal=[...preview.logo.history];
  const controller=new config.Controller({prepare:(value,current)=>config.preparePreview(preview,value,current)});
  await assert.rejects(controller.apply({...changed,reduced:false}),/Retry the failed preview/);
  assert.equal(preview.state,state);assert.deepEqual(preview.logo.history,journal);assert.deepEqual(controller.active,config.defaults);
  fail=false;await preview.recover();await controller.apply({...changed,reduced:false});assert.equal(state.isSolved(),true);assert.equal(preview.order,3);
  assert.equal(brand.geometry(3).gap,4);
});
test('rapid reduced-motion and order changes restore once and commit only the final real preview',async()=>{
  const gate=deferred(),started=deferred(),mounted=[];let block=false,blocked=false;
  const preview=new Preview({mount:state=>{mounted.push(state);return {...animator(),turn:async()=>{if(block&&!blocked){blocked=true;started.resolve();await gate.promise;}}};}});
  await preview.run('scramble');const before=preview.state;block=true;
  const commits=[],controller=new config.Controller({prepare:(value,current)=>config.preparePreview(preview,value,current),commit:value=>commits.push(value)});
  const first=controller.apply(changed);await started.promise;
  const lastValue={...config.defaults,variant:'monochrome',pngSize:512},last=controller.apply(lastValue);
  gate.resolve();assert.deepEqual(await Promise.all([first,last]),[false,true]);
  assert.deepEqual(commits,[lastValue]);assert.equal(preview.order,2);assert.equal(preview.reduced,false);assert.equal(preview.state,before);
  assert.equal(before.isSolved(),true);assert.equal(preview.logo.history.length,0);assert.equal(mounted.length,1);
});
