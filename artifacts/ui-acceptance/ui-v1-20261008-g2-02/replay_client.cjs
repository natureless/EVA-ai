// Replay actual API transcripts through shipped Chat and real cube logic.
// DOM/animation timing is controlled; this does not measure browser paint.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../..');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const harnessFile=path.join(root,'tests/js/test_cube_chat.cjs');
const harnessSource=fs.readFileSync(harnessFile,'utf8').split('async function failRealChat')[0];
const flush=()=>new Promise(resolve=>setImmediate(resolve));

(async()=>{
  const results=[];
  for(const item of fixture.cases){
    const harnessContext={require:createRequire(harnessFile),__dirname:path.dirname(harnessFile),
      console,Event,setImmediate};
    vm.runInNewContext(harnessSource+'\nthis.h=harness(false,true);',harnessContext);
    const h=harnessContext.h;
    await h.cube.setConversationMode(item.mode);
    let seed=17;h.logo.rng=()=>((seed=(seed*1664525+1013904223)>>>0)/4294967296);
    const originalLogo=h.logo,initial=originalLogo.state.snapshot();
    const timers=new Map(),calls=[];let seq=0,release;
    const gate=new Promise(resolve=>release=resolve);
    const fetch=async(url,options={})=>{
      calls.push({url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});
      if(options.method==='POST'){
        await gate;
        assert.equal(JSON.parse(options.body).mode,item.mode);
        if(item.transport==='WS')return {ok:true,status:200,json:async()=>item.ack};
        const bytes=Buffer.from(item.wire);let position=0;
        return {ok:true,status:200,headers:{get:n=>n.toLowerCase()==='x-eva-task-id'?item.headerTaskId:'text/event-stream'},
          body:{getReader:()=>({read:async()=>{if(position===bytes.length)return {done:true};
            const value=bytes.subarray(position,position+7);position+=value.length;return {done:false,value};},
            cancel:async()=>{},releaseLock(){}})}};
      }
      context.chat._onWSMessage(item.wsMessage);
      return {ok:true,status:200,json:async()=>item.polled};
    };
    const context={document:h.document,ParticleController:h.cube,Event,TextDecoder,AbortController,fetch,
      I18N:{lang:()=> 'zh'},setTimeout(fn){timers.set(++seq,fn);return seq;},clearTimeout(id){timers.delete(id);}};
    vm.runInNewContext(fs.readFileSync(path.join(root,'ui/web/static/http.js'),'utf8'),context);
    const source=fs.readFileSync(path.join(root,'ui/web/static/chat.js'),'utf8');
    vm.runInNewContext(source.slice(0,source.indexOf('// ── Init'))+'\nthis.chat=Chat;',context);
    const chat=context.chat;chat._renderMessages=()=>{};
    chat._addMessage=(role,text,mode)=>chat._messages.push({role,text,mode});
    const submission=chat._submit(item.request.text,item.mode,item.transport==='SSE',false);
    for(let n=0;n<60&&!originalLogo.history.length;n++)await flush();
    assert.ok(originalLogo.history.length>0,'real legal moves must have been committed');
    const activeSnapshot=originalLogo.state.snapshot();
    const activeMoves=[...originalLogo.history];
    const activeOrder=h.cube._order;
    assert.equal(activeOrder,item.mode==='deep'?3:2);
    await h.cube.setConversationMode(item.selectedNextMode);
    assert.equal(h.cube._order,activeOrder,'next selection cannot replace an active cube');
    assert.equal(h.cube._activeResponseMode,item.mode);
    h.ids.chatInput.value='处理中继续编辑的草稿';
    release();await submission;
    const reply=chat._messages.find(m=>m.role==='eva');
    assert.equal(reply.mode,item.mode);assert.equal(reply.status,'completed');
    assert.equal(reply.taskId,item.taskId);assert.equal(reply.text,item.receipt.reply);
    const settle=chat._settleTimer;assert.ok(timers.has(settle));
    const finish=timers.get(settle);timers.delete(settle);finish();
    for(let n=0;n<60&&(h.cube._exitPromise||originalLogo.busy);n++)await flush();
    assert.deepEqual(originalLogo.state.snapshot(),initial);
    assert.equal(originalLogo.history.length,0);assert.equal(originalLogo.state.isSolved(),true);
    assert.equal(h.cube._order,item.selectedNextMode==='deep'?3:2);
    assert.equal(h.logo.state.isSolved(),true);
    assert.equal(h.ids.chatInput.value,'处理中继续编辑的草稿');
    assert.equal(calls.filter(c=>c.method==='POST').length,1);
    assert.equal(h.errors.length,0);
    results.push({mode:item.mode,transport:item.transport,taskId:item.taskId,activeOrder,
      initial,activeSnapshot,activeMoves,activeModeStayedFixed:true,replyStatus:reply.status,
      finalActiveSnapshot:originalLogo.state.snapshot(),finalActiveSolved:true,
      nextOrder:h.cube._order,nextSolved:true,draftPreserved:true,networkReplay:calls});
  }
  process.stdout.write(JSON.stringify({status:'passed',cases:results,
    scope:'Actual API transcript replay through shipped Chat/ParticleController/CubeLogo/CubeState; controlled DOM and animator, no browser paint'},null,2)+'\n');
})().catch(error=>{console.error(error);process.exitCode=1;});
