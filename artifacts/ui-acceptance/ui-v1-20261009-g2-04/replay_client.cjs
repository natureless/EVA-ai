// Replay actual isolated API bodies into shipped clients; DOM/paint are controlled.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../../..');
const api=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
assert.equal(api.status,'passed');
const responses=api.responses,checks=[];
function check(name,condition,actual){assert.ok(condition,name);checks.push({name,passed:true,actual});}
function reuse(name,cut){
  const file=path.join(root,'tests/js',name);
  const code=fs.readFileSync(file,'utf8').split(cut)[0];
  const context={require,__dirname:path.dirname(file),setImmediate,Response,AbortController,DOMException,URLSearchParams,console};
  vm.runInNewContext(code+'\nglobalThis.replayHarness=harness;globalThis.replayFlush=flush;',context);
  return context;
}
async function main(){
  const list=reuse('test_memory_explorer.cjs',"test('late tier");
  const h=list.replayHarness();h.respond(0,responses.tiers.body);await list.replayFlush();
  const browse=h.explorer._selectTier('S2');h.respond(1,responses.entries.body);await browse;
  check('list actual browse',h.nodes.get('memEntries').innerHTML.includes('G2隔离标记'),{request:h.requests[1].url});
  h.nodes.get('memSearch').value='G2隔离标记';h.click('memSearchBtn');h.respond(2,responses.search.body);await list.replayFlush();
  check('list actual search',h.nodes.get('memEntries').innerHTML.includes('G2隔离标记'),{total:responses.search.body.total,scope:'Recorded backend searched S2; shipped client searches S1–S4. One seeded result is replayed; this is not a live end-to-end search.'});
  const graph=reuse('test_memory_graph_interactions.cjs','async function loaded()');
  for(const dimension of ['2d','3d']){
    const g=graph.replayHarness(1200,null,null,dimension);
    g.requests[0].resolve(responses.graph.body);await graph.replayFlush();
    for(let i=1;i<=140;i++)g.frame(i*40);
    check(dimension+' real graph nodes',Number(g.get('listCount').textContent)===responses.graph.body.nodes.length,{nodes:g.get('listCount').textContent});
    const index=g.get('nodeList').children[0].children.findIndex(n=>n.dataset.nodeId==='S4:g2-world-0');
    assert.ok(index>=0);g.choose(index);g.requests.at(-1).resolve(responses.detail.body);await graph.replayFlush();
    check(dimension+' real detail',g.get('detailTitle').textContent==='G2隔离实体0',{title:g.get('detailTitle').textContent});
    g.get('localView').onclick();g.get('loadNeighbors').onclick();g.requests.at(-1).resolve(responses['neighbors-full'].body);await graph.replayFlush();
    check(dimension+' actual neighbor read completed',g.get('loadNeighbors').disabled===true,{status:g.get('neighborReadStatus').textContent});
    g.get('zoomIn').onclick();
    // Disable decorative travelling edge particles for a node-position comparison.
    g.get('animateGraph').checked=false;
    // Settle the new local layout before attributing any position change to theme.
    for(let i=0;i<=140;i++)g.frame(6000+i*40);
    const before={zoom:g.get('zoomLevel').textContent,title:g.get('detailTitle').textContent,count:g.get('listCount').textContent,requests:g.requests.length,positions:JSON.stringify(g.arcs.map(a=>a.slice(0,2)))};
    g.theme();g.frame(11640);
    check(dimension+' theme no read or state loss',g.requests.length===before.requests&&g.get('zoomLevel').textContent===before.zoom&&g.get('detailTitle').textContent===before.title&&g.get('listCount').textContent===before.count,{before,after:{requests:g.requests.length,zoom:g.get('zoomLevel').textContent,title:g.get('detailTitle').textContent}});
    if(dimension==='2d') {
      const afterPositions=JSON.stringify(g.arcs.map(a=>a.slice(0,2)));
      if(afterPositions!==before.positions) fs.writeFileSync(path.join(__dirname,'replay-position-diagnostic.json'),JSON.stringify({before:JSON.parse(before.positions),after:JSON.parse(afterPositions)},null,2));
      check('2d theme preserves rendered positions',afterPositions===before.positions,{});
    }
    g.get('reloadGraph').onclick();g.requests.at(-1).reject(new Error('HTTP 503'));await graph.replayFlush();
    check(dimension+' refresh failure preserves local view',g.get('zoomLevel').textContent===before.zoom&&g.get('detailTitle').textContent===before.title&&g.get('localView').classList.contains('chosen'),{message:g.get('graphMessage').textContent});
    g.get('reloadGraph').onclick();g.requests.at(-1).resolve(responses['recovered-graph'].body);await graph.replayFlush();
    g.requests.at(-1).resolve(responses.detail.body);await graph.replayFlush();
    check(dimension+' refresh recovery retains selection',g.get('graphMessage').hidden===true&&g.get('detailTitle').textContent===before.title,{title:g.get('detailTitle').textContent});
  }
  const context={AbortController,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'ui/web/static/runtime_status.js'),'utf8'),context);
  const element={dataset:{}};
  await context.EvaRuntimeStatus.refresh(element,async()=>Response.json(responses.runtime.body));
  check('actual runtime recognized',element.dataset.runtimeState==='running',{text:element.textContent});
  await context.EvaRuntimeStatus.refresh(element,async()=>Response.json({detail:'unavailable'},{status:503}));
  check('runtime failure clears running state',element.dataset.runtimeState==='unknown',{text:element.textContent});
  const result={status:'passed',apiEvidence:path.relative(root,path.resolve(process.argv[2])).replaceAll('\\','/'),checks,
    scope:'Actual API body replay, shipped JS, controlled DOM/canvas/timers; not browser paint, real disconnect, native or screen-reader verification'};
  fs.writeFileSync(path.join(__dirname,'client-replay.json'),JSON.stringify(result,null,2)+'\n');
  process.stdout.write(JSON.stringify({status:result.status,checks:checks.length})+'\n');
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
