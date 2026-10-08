const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../ui/web/static/memory_graph.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const world = {id:'S4:world', record_id:'world', tier:'S4', label:'World', content:'summary', provenance:{}};
const other = {id:'S4:other', record_id:'other', tier:'S4', label:'Other', content:'summary', provenance:{}};
function graph(nodes = [world, other], query = '') {
  return {nodes, edges:[], query, counts:Object.fromEntries(['S1','S2','S3','S4','S5'].map(t => [t,
    {available:true,total:nodes.filter(n => n.tier === t).length}])),
    generated_at:'2026-09-25T00:00:00Z', scope:{matched_nodes:nodes.length}};
}
function harness(innerWidth=1200, themePalette=null, layout=null, dimension='2d',motion={}) {
  const dom = new Map(), documentEvents = {}, raf = new Map(), timers = new Map(), requests = [];
  const captured=new Set(),resizeObservers=new Map();
  let serial = 0, observer, draws = 0;
  const arcs = [], lines=[];let pen=null;
  function element(tag = 'div') {
    const classes = new Set();
    return {tagName:tag.toUpperCase(), textContent:'', children:[], events:{}, checked:false,
      hidden:false, value:'', scrollTop:0, dataset:{}, open:false, disabled:false,
      style:{values:{},setProperty(key,value){this.values[key]=value;}},
      querySelectorAll(selector){const all=[];const visit=node=>{if(selector==='[data-node-id]'&&node.dataset?.nodeId)all.push(node);for(const child of node.children||[])visit(child);};visit(this);return all;},
      querySelector(selector){return this.children.find(child=>selector==='.'+child.className)||null;},
      classList:{add:x => classes.add(x), remove:x => classes.delete(x),
        contains:x => classes.has(x), toggle(x,value = !classes.has(x)) {value ? classes.add(x) : classes.delete(x);}},
      setAttribute(){}, focus(){document.activeElement = this;},
      append(...children){this.children.push(...children);},
      replaceChildren(...children){this.children=children;this.scrollTop=0;},
      addEventListener(name,callback){this.events[name]=callback;},
      showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},
      getBoundingClientRect(){return {width:900,height:700,left:0,top:0};},
      setPointerCapture(){},
    };
  }
  const get = id => {if(!dom.has(id))dom.set(id,element());return dom.get(id);};
  const workspace = element();
  const document = {hidden:false,documentElement:element('html'),activeElement:element('body'),getElementById:get,
    createElement:element,createDocumentFragment:element,querySelector:() => workspace,
    addEventListener:(name,fn) => {documentEvents[name] = fn;}};
  for(const id of ['showOrigins','showOrphans','showLabels'])get(id).checked=true;
  get('edgeBrightness').value='38';
  get('displayPanel').hidden=true;
  get('graphDimension').value=dimension;get('graphNavigation').value='rotate';get('showOrbits').checked=true;
  get('cameraMotion').checked=Boolean(motion.enabled);
  const motionPreference={matches:Boolean(motion.reduced),changed:null,addEventListener(name,callback){if(name==='change')this.changed=callback;}};
  const canvas=get('memoryCanvas');
  get('inspector').getBoundingClientRect=()=>layout?.panel||{left:0,top:0,width:0,height:0};
  if(layout)canvas.getBoundingClientRect=()=>layout.canvas;
  canvas.setPointerCapture=id=>captured.add(id);canvas.hasPointerCapture=id=>captured.has(id);
  canvas.releasePointerCapture=id=>{captured.delete(id);canvas.events.lostpointercapture?.({pointerId:id});};
  get('memoryCanvas').getContext = () => new Proxy({}, {get:(_,key) => {
    if(key==='clearRect')return () => {draws++;arcs.length=0;lines.length=0;};
    if(key==='moveTo')return (x,y) => {pen={x,y};};
    if(key==='lineTo')return (x,y) => {if(pen)lines.push({a:pen,b:{x,y}});};
    if(key==='arc')return (...args) => arcs.push(args);
    if(key==='createRadialGradient')return () => ({addColorStop(){}});
    if(key==='measureText')return text => ({width:text.length*5});
    return () => {};
  }});
  const context = {document,console,URLSearchParams,AbortController,devicePixelRatio:1,innerWidth,
    addEventListener:(name,fn)=>{documentEvents['window:'+name]=fn;},
    matchMedia:() => motionPreference,ResizeObserver:class {constructor(callback){this.callback=callback;}observe(el){resizeObservers.set(el,this.callback);}},
    MutationObserver:class {constructor(callback){observer=callback;}observe(){}},
    requestAnimationFrame:fn => {const id=++serial;raf.set(id,fn);return id;},
    cancelAnimationFrame:id => raf.delete(id),
    setTimeout:(fn,ms) => {const id=++serial;timers.set(id,{fn,ms});return id;},
    clearTimeout:id => timers.delete(id),
    EvaHttp:{json:(url,options) => new Promise((resolve,reject) => requests.push({url,options,resolve,reject}))},
  };
  if(themePalette)context.getComputedStyle=()=>({getPropertyValue:key=>themePalette[key]||'#777777'});
  const bodies=[];let spatialDraw;
  if(dimension==='3d'){
    for(const file of ['memory_galaxy.js','memory_galaxy_renderer.js'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../ui/web/static',file),'utf8'),context);
    const renderer=context.EvaMemoryGalaxyRenderer,draw=renderer.draw;
    context.EvaMemoryGalaxyRenderer={...renderer,draw(ctx,opts){spatialDraw=opts;const result=draw(ctx,opts);bodies.splice(0,bodies.length,...result.bodies);return result;}};
  }
  vm.runInNewContext(source,context);
  return {get,requests,workspace,document,raf,timers,arcs,lines,captured,draws:() => draws,
    bodies,spatial:()=>spatialDraw,
    reducedMotion(value){motionPreference.matches=value;motionPreference.changed?.();},
    resize(id){resizeObservers.get(get(id))();},
    frame(time){const callbacks=[...raf.values()];raf.clear();callbacks.forEach(fn => fn(time));},
    dialog(open){get('relationDiagnostics').open=open;observer();},
    visible(visible){document.hidden=!visible;documentEvents.visibilitychange();},
    theme(){documentEvents['window:theme-changed']();},
    key(event){documentEvents.keydown(event);},
    createdGoal(id){return documentEvents['window:eva:goal-created']({detail:{goal_id:id}});},
    timer(ms){for(const [id,timer] of [...timers])if(timer.ms===ms){timers.delete(id);timer.fn();}},
    choose(index=0){get('nodeList').children[0].children[index].events.click();},
    inspect(index=0){get('neighbors').children[0].children[index].children[1].events.click();},
  };
}
async function loaded() {
  const h=harness();h.requests[0].resolve(graph());await flush();
  // Finish initial layout so later camera comparisons measure navigation only.
  for(let i=1;i<=140;i++)h.frame(i*40);
  return h;
}

async function overlayGraph(innerWidth,layout){
  const h=harness(innerWidth,null,layout);h.requests[0].resolve(graph([world]));await flush();
  for(let i=1;i<=140;i++)h.frame(i*40);
  return h;
}

test('Escape closes display settings before clearing selection and returns focus to its trigger',async()=>{
  const h=await loaded();h.choose();const title=h.get('detailTitle').textContent,requests=h.requests.length;
  const input=h.get('showLabels');h.get('displayPanel').querySelector=()=>input;
  h.get('displayToggle').onclick();assert.equal(h.document.activeElement,input);
  h.key({key:'Escape',preventDefault(){}});
  assert.equal(h.get('displayPanel').hidden,true);assert.equal(h.document.activeElement,h.get('displayToggle'));
  assert.equal(h.get('detailTitle').textContent,title);assert.equal(h.workspace.classList.contains('has-selection'),true);
  assert.equal(h.requests.length,requests);
});
test('search shortcut preserves textarea and editable text, and ignores IME composition',async()=>{
  const h=await loaded();let prevented=0;
  for(const active of [{tagName:'TEXTAREA'},{tagName:'SELECT'},{tagName:'DIV',isContentEditable:true}]){
    h.document.activeElement=active;h.key({key:'/',preventDefault(){prevented++;}});
    assert.equal(h.document.activeElement,active);
  }
  h.key({key:'k',ctrlKey:true,isComposing:true,preventDefault(){prevented++;}});assert.equal(prevented,0);
  h.document.activeElement={tagName:'BODY'};h.key({key:'/',preventDefault(){prevented++;}});
  assert.equal(h.document.activeElement,h.get('graphSearch'));assert.equal(prevented,1);
});

test('Escape dismisses camera options without clearing the selected node or changing navigation mode',async()=>{
  const h=await loaded();h.choose();const title=h.get('detailTitle').textContent,summary=h.get('cameraSummary');
  h.get('cameraOptions').open=true;h.get('cameraOptions').querySelector=()=>summary;
  h.get('graphNavigation').value='pan';const requests=h.requests.length;
  h.key({key:'Escape',preventDefault(){}});
  assert.equal(h.get('cameraOptions').open,false);assert.equal(h.document.activeElement,summary);
  assert.equal(h.get('detailTitle').textContent,title);assert.equal(h.workspace.classList.contains('has-selection'),true);
  assert.equal(h.get('graphNavigation').value,'pan');assert.equal(h.requests.length,requests);
});

test('local filters start compact on phones and retain the chosen disclosure across navigation',async()=>{
  const {h}=await linked(390);assert.equal(h.get('localFilterPanel').open,false);
  h.choose();h.get('localView').onclick();assert.equal(h.get('localFilterPanel').open,false);
  h.get('localFilterPanel').open=true;h.get('localFilterPanel').events.toggle();
  h.choose(1);h.get('recenterLocal').onclick();assert.equal(h.get('localFilterPanel').open,true);
  h.get('globalView').onclick();h.get('localView').onclick();assert.equal(h.get('localFilterPanel').open,true);
  h.get('localFilterPanel').open=false;h.get('localFilterPanel').events.toggle();
  h.get('expandLocal').onclick();assert.equal(h.get('localFilterPanel').open,false);
  assert.equal(harness(1200).get('localFilterPanel').open,true);
});

test('filter disclosure keeps zoom for either observer order and cancels old input without refetching',async()=>{
  const layout=phoneLayout(),h=await overlayGraph(390,layout),canvas=h.get('memoryCanvas');
  h.choose();h.get('localView').onclick();h.get('zoomIn').onclick();h.frame(5700);
  const zoom=h.get('zoomLevel').textContent,requests=h.requests.length;
  pointer(canvas,'pointerdown',1,150,300);pointer(canvas,'pointerdown',2,250,300);
  h.get('localFilterPanel').open=true;layout.canvas.height=410;
  h.resize('memoryCanvas');h.get('localFilterPanel').events.toggle();h.frame(5800);
  assert.equal(h.get('zoomLevel').textContent,zoom);assert.equal(h.captured.size,0);
  h.get('localFilterPanel').open=false;layout.canvas.height=690;
  h.get('localFilterPanel').events.toggle();h.resize('memoryCanvas');h.frame(5900);
  assert.equal(h.get('zoomLevel').textContent,zoom);assert.equal(h.requests.length,requests);
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('detailTitle').textContent,'World');
});

test('collapsed filter summary shows exact active filters and resets without broadening silently',async()=>{
  const {h}=await linked(390);h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();
  h.get('localDirection').value='outgoing';h.get('localKind').value='stored_relation';
  const relation='<img src=x onerror=alert(1)>';h.get('localRelation').value=JSON.stringify(['stored_relation',relation]);h.get('localRelation').onchange();
  assert.equal(h.get('localFilterPanel').open,false);assert.match(h.get('localFilterSummary').textContent,/2 层 · 沿出向 · 已存储关系/);
  assert.ok(h.get('localFilterSummary').textContent.includes(relation));assert.equal(h.get('localFilterSummary').title,h.get('localFilterSummary').textContent);
  assert.equal(h.get('listCount').textContent,1);assert.match(h.get('localLoadedScope').textContent,/已加载的 3 个节点/);
  h.get('resetLocalFilters').onclick();assert.equal(h.get('localFilterPanel').open,false);
  assert.equal(h.get('localFilterSummary').textContent,'2 层 · 双向 · 全部连接 · 全部名称');assert.equal(h.get('listCount').textContent,3);
});

test('refresh and goal mode retain compact filters and keep scope limitations outside the disclosure',async()=>{
  const {h,data}=await linked(390);h.choose();h.get('localView').onclick();h.get('reloadGraph').onclick();
  h.requests.at(-1).resolve(data);await flush();assert.equal(h.get('localFilterPanel').open,false);
  assert.match(h.get('localLoadedScope').textContent,/已加载的 3 个节点/);
  goals(h);h.requests.at(-1).resolve(goalGraph());await flush();h.choose();h.get('localView').onclick();
  assert.equal(h.get('localFilterPanel').open,false);assert.equal(h.get('loadNeighbors').hidden,true);
  assert.equal(h.get('localLoadedScope').textContent,'仅探索当前页已加载的记录和输入引用。');
});
const phoneLayout=()=>({canvas:{left:43,top:123,width:347,height:690},panel:{left:43,top:434,width:347,height:379}});

test('phone selection and button zoom use the canvas above the drawer without resetting scale',async()=>{
  const layout=phoneLayout(),h=await overlayGraph(390,layout),zoom=h.get('zoomLevel').textContent;
  h.choose();h.frame(5700);
  assert.equal(h.get('zoomLevel').textContent,zoom);
  assert.equal(h.arcs[0][0],347/2);assert.equal(h.arcs[0][1],311/2);
  assert.equal(h.workspace.style.values['--graph-view-bottom'],'379px');
  h.get('zoomIn').onclick();h.frame(5800);
  assert.ok(Math.abs(h.arcs[0][0]-347/2)<1e-9);assert.ok(Math.abs(h.arcs[0][1]-311/2)<1e-9);
  assert.notEqual(h.get('zoomLevel').textContent,zoom);
});

test('tablet drawer shifts the camera and controls left; a desktop inspector does not',async()=>{
  for(const [innerWidth,expectedWidth] of [[1015,476],[1400,762]]){
    const layout={canvas:{left:253,top:123,width:762,height:689},panel:{left:729,top:51,width:286,height:840}};
    const h=await overlayGraph(innerWidth,layout);h.choose();h.frame(5700);
    assert.equal(h.arcs[0][0],expectedWidth/2);assert.equal(h.arcs[0][1],689/2);
    assert.equal(h.workspace.style.values['--graph-view-right'],(762-expectedWidth)+'px');
  }
});

test('late detail size changes keep the selected node visible without fetching again or resetting zoom',async()=>{
  const layout=phoneLayout(),h=await overlayGraph(390,layout);h.choose();h.frame(5700);
  const requestCount=h.requests.length,zoom=h.get('zoomLevel').textContent;
  layout.panel.top=700;layout.panel.height=113;h.resize('inspector');h.frame(5800);
  assert.equal(h.arcs[0][1],577/2);
  layout.panel.top=253;layout.panel.height=560;h.resize('inspector');h.frame(5900);
  assert.ok(h.arcs[0][1]>=24-1e-9&&h.arcs[0][1]<=50+1e-9);
  assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.requests.length,requestCount);
  assert.equal(h.get('zoomLevel').textContent,zoom);
});

test('opening and closing local details preserves the local center and camera scale',async()=>{
  const layout=phoneLayout(),h=await overlayGraph(390,layout);h.choose();h.get('localView').onclick();h.frame(5700);
  layout.canvas.top=533;layout.canvas.height=280;layout.panel.top=659;layout.panel.height=154;
  h.resize('memoryCanvas');h.frame(5800);
  const zoom=h.get('zoomLevel').textContent,requests=h.requests.length;
  h.get('toggleLocalDetail').onclick();h.frame(5900);
  assert.equal(h.get('zoomLevel').textContent,zoom);
  assert.equal(h.workspace.style.values['--graph-view-bottom'],'154px');
  assert.ok(h.arcs[0][1]>=24&&h.arcs[0][1]<=50);assert.equal(h.get('localCenterLabel').textContent,'World');
  h.get('fitGraph').onclick();h.frame(6000);assert.ok(h.arcs[0][1]<=50);
  const fitted=h.get('zoomLevel').textContent;
  h.get('toggleLocalDetail').onclick();h.frame(6100);
  assert.equal(h.workspace.style.values['--graph-view-bottom'],'0px');assert.equal(h.get('zoomLevel').textContent,fitted);
  assert.equal(h.requests.length,requests);assert.equal(h.get('detailTitle').textContent,'World');
});

test('a canvas tap keeps its selected node above the drawer; clearing restores the full canvas',async()=>{
  const layout=phoneLayout(),h=await overlayGraph(390,layout),canvas=h.get('memoryCanvas');
  const [x,y]=h.arcs[0];pointer(canvas,'pointerdown',1,x+43,y+123);pointer(canvas,'pointerup',1,x+43,y+123);h.frame(5700);
  assert.equal(h.get('detailTitle').textContent,'World');assert.ok(h.arcs[0][1]<=231);
  const zoom=h.get('zoomLevel').textContent;
  h.get('clearSelection').onclick();h.frame(5800);
  assert.equal(h.workspace.style.values['--graph-view-bottom'],'0px');assert.equal(h.get('zoomLevel').textContent,zoom);
  assert.equal(h.workspace.classList.contains('has-selection'),false);
});

test('theme changes recolor the existing graph without reloading, replacing focused nodes or moving the camera',async()=>{
  const palette={'--ui-tier-s4':'#705391'},h=harness(1200,palette);
  h.requests[0].resolve(graph());await flush();for(let i=1;i<=140;i++)h.frame(i*40);
  h.choose();h.frame(5700);
  const button=h.get('nodeList').children[0].children[0];h.document.activeElement=button;
  const before={title:h.get('detailTitle').textContent,zoom:h.get('zoomLevel').textContent,requests:h.requests.length,
    positions:h.arcs.map(([x,y])=>[x,y])};
  palette['--ui-tier-s4']='#c4b0dc';h.theme();h.frame(5800);
  assert.equal(h.get('nodeList').children[0].children[0],button);assert.equal(h.document.activeElement,button);
  assert.equal(button.children[0].style.values['--tier-color'],'#c4b0dc');
  assert.equal(h.get('detailTitle').textContent,before.title);assert.equal(h.get('zoomLevel').textContent,before.zoom);
  assert.equal(h.requests.length,before.requests);assert.deepEqual(h.arcs.map(([x,y])=>[x,y]),before.positions);
});

function goalGraph(offset=0,next=null){
  const nodes=[
    {id:'G:g',tier:'G',record_id:'g',label:'<img src=x onerror=alert(1)>',kind:'business_goal',status:'pending_verification',record:{version:3,task_id:'task',source_event_id:'event',success_conditions:[{field:'checks.files_match',expected:true}]},verification_count:7,history_truncated:true,notice:'已存快照，不触发检查'},
    {id:'R:g',tier:'R',record_id:'g',label:'请求回执',kind:'processing_receipt',status:'succeeded',record:{task_id:'task',event_id:'event',terminal_state:'succeeded'}},
    {id:'V:v',tier:'V',record_id:'v',label:'文件检查',kind:'file_verification',status:'unknown',record:{observed_at:'2026-09-27',base_goal_version:3,committed_goal_version:4,files:[{path:'artifact.txt',expected_sha256:'a'.repeat(64),observed_sha256:null,outcome:'unknown',reason:'read_error'}]},notice:'采样时刻，非代码质量保证'},
  ];
  return {...graph(nodes),counts:{G:{available:true,total:1},R:{available:true,total:1},V:{available:true,total:1}},
    edges:[{source:'G:g',target:'R:g',kind:'goal_evidence',relation:'processing_receipt'}, {source:'G:g',target:'V:v',kind:'goal_evidence',relation:'verification_run'}],
    scope:{matched_nodes:3,matched_goals:41,goal_offset:offset,goal_limit:40,history_truncated:true},next_offset:next};
}
function goals(h){h.get('graphSource').value='goals';h.get('graphSource').onchange();}

test('stored Episode node traces state revisions and real edge, with no mutation or replay on inspection',async()=>{
  const h=await loaded();goals(h);
  const data=goalGraph();
  data.nodes[0].record.episode_reference={status:'available',episode_id:'ep-1'};
  data.nodes.push({id:'E:ep-1',tier:'E',record_id:'ep-1',kind:'processing_episode',label:'执行记录',status:'episode_succeeded',
    record:{episode_id:'ep-1',event_id:'event',started_at:'2030-01-01',state_before:{version:4},state_after:{version:5},
      policy_version:'policy-v1',strategy_version:'strategy-v1',action_count:20,actions_truncated:true,fields_truncated:true,
      result_receipt_id:'receipt',external_actions_replayed:false},notice:'查看不会重放动作'});
  data.edges.push({source:'G:g',target:'E:ep-1',kind:'goal_evidence',relation:'source_event_episode'});
  data.counts.E={available:true,total:1};
  h.requests.at(-1).resolve(data);await flush();
  const before=h.requests.length;h.choose();
  assert.match(h.get('goalSummary').textContent,/已关联 ep-1/);
  h.get('localView').onclick();h.inspect(2);
  assert.equal(h.get('relationName').textContent,'source_event_episode');
  assert.match(h.get('relationExplanation').textContent,/事件 ID 一致/);
  assert.match(h.get('relationExplanation').textContent,/不会重放动作/);
  h.get('relationTo').onclick();
  assert.equal(h.get('goalStatus').textContent,'执行记录：成功');
  assert.match(h.get('goalSummary').textContent,/状态 revision：4 → 5/);
  assert.match(h.get('goalSummary').textContent,/仅展示前 16 个/);
  assert.match(h.get('goalSummary').textContent,/执行成功不等于目标完成/);
  assert.equal(h.get('verifyGoal').hidden,true);assert.equal(h.get('cancelGoal').hidden,true);
  assert.equal(h.get('loadGoalHistory').hidden,true);assert.equal(h.requests.length,before);
});

test('processor Episode shows real snapshot hashes with unknown revision and labels recovery sealing time',async()=>{
  const h=await loaded();goals(h);const data=goalGraph();
  data.nodes.push({id:'E:ep-2',tier:'E',record_id:'ep-2',label:'恢复执行记录',status:'episode_unknown',kind:'processing_episode',
    record:{episode_id:'ep-2',event_id:'event',state_reference_kind:'world_snapshot_unversioned',completion_kind:'recovery_unknown',
      started_at:'2030-01-01',completed_at:'2030-01-02',state_before:{version:null,integrity_hash:'hash-before'},state_after:null,
      action_count:1,actions:[],external_actions_replayed:false}});
  data.counts.E={available:true,total:1};
  h.requests.at(-1).resolve(data);await flush();const before=h.requests.length;h.choose(3);
  assert.match(h.get('goalSummary').textContent,/世界快照：版本未知/);
  assert.match(h.get('goalSummary').textContent,/处理前：hash-before/);
  assert.match(h.get('goalSummary').textContent,/记录封存时间/);
  assert.match(h.get('goalSummary').textContent,/重启恢复，执行结果未知/);
  assert.doesNotMatch(h.get('goalSummary').textContent,/状态 revision：0/);
  assert.equal(h.requests.length,before);
});

test('missing and invalid Episode references remain distinct and do not create nodes or request unknown detail routes',async()=>{
  for(const reference of [{status:'not_recorded',reason:'store_missing'}, {status:'not_recorded',reason:'event_missing'},
    {status:'unavailable',reason:'identity_mismatch'}]){
    const h=await loaded();goals(h);const data=goalGraph();
    data.nodes[0].record.episode_reference=reference;
    data.scope.episode_references_unavailable=reference.status==='unavailable'?1:0;
    h.requests.at(-1).resolve(data);await flush();const count=h.requests.length;h.choose();
    assert.match(h.get('goalSummary').textContent,reference.status==='unavailable'?/引用不可用/:/未接入 Episode|未记录 Episode/);
    assert.equal(h.get('listCount').textContent,3);assert.equal(h.requests.length,count);
    if(reference.status==='unavailable')assert.match(h.get('scopeStatus').textContent,/1 个执行引用不可用/);
  }
});

test('Episode tool observations show receipt identities and unknown interruption without replay requests',async()=>{
  const h=await loaded();goals(h);const data=goalGraph();
  data.nodes.push({id:'E:ep-tool',tier:'E',record_id:'ep-tool',label:'工具执行记录',status:'episode_unknown',kind:'processing_episode',
    record:{episode_id:'ep-tool',event_id:'event',action_count:2,tool_receipts:{status:'available',receipts:[
      {tool_id:'tool:probe',receipt_id:'receipt-1',status:'unknown',observation_kind:'seal_unknown',request_hash:'hash-1',finished_at:null}
    ]}}});
  data.counts.E={available:true,total:1};h.requests.at(-1).resolve(data);await flush();const count=h.requests.length;h.choose(3);
  assert.match(h.get('goalSummary').textContent,/tool:probe · 结果未知/);
  assert.match(h.get('goalSummary').textContent,/收据：receipt-1/);
  assert.match(h.get('goalSummary').textContent,/请求哈希：hash-1/);
  assert.match(h.get('goalSummary').textContent,/不是完成时间/);
  assert.match(h.get('goalSummary').textContent,/不证明外部副作用/);
  assert.equal(h.requests.length,count);
});

function observedToolGraph(count=0,status='unknown'){
  const data=goalGraph();
  data.nodes.push({id:'E:ep-tool',tier:'E',record_id:'ep-tool',label:'工具执行记录',status:'episode_unknown',kind:'processing_episode',record:{episode_id:'ep-tool',event_id:'event',tool_receipts:{status:'available',receipts:[{
    action_id:'action',tool_id:'executor:file:write',receipt_id:'receipt',status,request_hash:'a'.repeat(64),reconciliation:{status:'available',count,intent:{path:'<fixed>.txt'},records:count?[{sequence:count,outcome:'passed',observed_at:'2030-01-01',files:[{path:'<fixed>.txt',outcome:'match',expected_sha256:'a'.repeat(64),observed_sha256:'a'.repeat(64)}]}]:[]}
  }]}}});
  data.counts.E={available:true,total:1};return data;
}
async function observedTools(count=0,status='unknown'){
  const h=await loaded();goals(h);h.requests.at(-1).resolve(observedToolGraph(count,status));await flush();h.choose(3);return h;
}
function toolRow(h){return h.get('toolChecks').children[0];}

test('independent file checks require an explicit click and send only stored identities and count',async()=>{
  const h=await observedTools(),row=toolRow(h),before=h.requests.length;
  assert.equal(row.children[0].textContent,'<fixed>.txt');assert.equal(h.requests.length,before);
  row.children[1].events.click();const request=h.requests.at(-1);
  assert.equal(request.url,'/api/tool-observations/action');assert.equal(request.options.method,'POST');
  assert.deepEqual(JSON.parse(request.options.body),{event_id:'event',expected_count:0});
  row.children[1].events.click();assert.equal(h.requests.length,before+1);
  request.resolve({outcome:'passed',sequence:1});await flush();
  const refresh=h.requests.at(-1);assert.match(refresh.url,/api\/goals\/graph/);refresh.resolve(observedToolGraph(1));await flush();
  assert.equal(h.get('goalStatus').textContent,'执行记录：结果未知');
  assert.match(h.get('goalSummary').textContent,/不改变原始执行结果或目标状态/);
  assert.equal(toolRow(h).children[1].disabled,false);
  assert.equal(toolRow(h).children[2].hidden,false);
});

test('reading all tool samples uses GET and does not trigger file sampling or graph refresh',async()=>{
  const h=await observedTools(4),row=toolRow(h),before=h.requests.length;
  row.children[2].events.click();const request=h.requests.at(-1);
  assert.equal(request.url,'/api/tool-observations/action?event_id=event');assert.equal(request.options.method,undefined);
  request.resolve({status:'available',count:4,records:[{sequence:1,outcome:'mismatch',observed_at:'2030-01-01',files:[{path:'<fixed>.txt',outcome:'mismatch',expected_sha256:'a'.repeat(64),observed_sha256:'b'.repeat(64)}]}]});await flush();
  assert.equal(h.requests.length,before+1);assert.match(row.children[4].textContent,/采样 1/);
  assert.match(row.children[3].textContent,/没有进行新的文件检查/);
});

test('late tool sample responses cannot refresh another selected node or switch data views',async()=>{
  const h=await observedTools(),row=toolRow(h);row.children[1].events.click();const request=h.requests.at(-1);
  h.choose(0);const before=h.requests.length;request.resolve({outcome:'passed'});await flush();
  assert.equal(h.requests.length,before);assert.equal(h.get('goalStatus').textContent,'目标待验证');
});

test('failed tool sample requests never retry automatically or infer an execution success',async()=>{
  const h=await observedTools(),row=toolRow(h);row.children[1].events.click();const request=h.requests.at(-1),before=h.requests.length;
  request.reject(new Error('storage unavailable'));await flush();
  assert.match(row.children[3].textContent,/页面不推断结果/);assert.equal(row.children[1].disabled,true);
  row.children[1].events.click();assert.equal(h.requests.length,before);assert.equal(h.get('goalStatus').textContent,'执行记录：结果未知');
});

test('completed tools and exhausted histories cannot submit a new file sample',async()=>{
  for(const [count,status] of [[1,'completed'],[32,'unknown']]){
    const h=await observedTools(count,status),row=toolRow(h),before=h.requests.length;
    assert.equal(row.children[1].hidden,true);row.children[1].events.click();assert.equal(h.requests.length,before);
  }
});

test('goal form pauses the graph and prevents global shortcuts from stealing form focus or clearing selection',async()=>{
  const h=await loaded();h.choose();h.get('goalCreateDialog').open=true;
  let intercepted=0;
  h.key({key:'Escape',preventDefault(){intercepted++;}});
  h.key({key:'k',ctrlKey:true,preventDefault(){intercepted++;}});
  const draws=h.draws();h.frame(5800);
  assert.equal(intercepted,0);assert.equal(h.get('detailContent').hidden,false);assert.equal(h.draws(),draws);
});

test('registered goal switches source, searches by ID and selects a real projected node without checking files',async()=>{
  const h=await loaded(),before=h.requests.length;
  const done=h.createdGoal('g');
  assert.equal(h.requests.length,before+1);
  const request=h.requests.at(-1);
  assert.match(request.url,/\/api\/goals\/graph\?limit=40&offset=0&q=g$/);
  request.resolve({...goalGraph(),query:'g'});await done;await flush();
  assert.equal(h.get('newGoal').hidden,false);assert.equal(h.get('graphSource').value,'goals');
  assert.equal(h.get('detailTitle').textContent,'<img src=x onerror=alert(1)>');
  assert.equal(h.requests.length,before+1);assert.equal(h.get('detailContent').hidden,false);
});
test('late creation refresh cannot switch the user back after a source change',async()=>{
  const h=await loaded(),done=h.createdGoal('g'),old=h.requests.at(-1);
  h.get('graphSource').value='memory';h.get('graphSource').onchange();
  h.requests.at(-1).resolve(graph());old.resolve({...goalGraph(),query:'g'});await done;await flush();
  assert.equal(h.get('graphSource').value,'memory');assert.equal(h.get('newGoal').hidden,true);
  assert.equal(h.get('detailContent').hidden,true);assert.equal(h.get('listCount').textContent,2);
});
test('creation refresh failure or missing projected goal never fabricates a node or completion',async()=>{
  const h=await loaded();let done=h.createdGoal('g');
  h.requests.at(-1).reject(new Error('offline'));await done;
  assert.equal(h.get('detailContent').hidden,true);assert.match(h.get('graphMessage').textContent,/连接|offline/);
  done=h.createdGoal('g');const empty=goalGraph();empty.nodes=[];empty.edges=[];empty.query='g';
  h.requests.at(-1).resolve(empty);await done;
  assert.equal(h.get('listCount').textContent,0);assert.equal(h.get('detailContent').hidden,true);
});

test('goal mode renders real references, distinct outcomes and file evidence without invoking verification',async()=>{
  const h=await loaded();goals(h);
  assert.match(h.requests.at(-1).url,/^\/api\/goals\/graph\?limit=40&offset=0/);
  h.requests.at(-1).resolve(goalGraph());await flush();
  assert.equal(h.get('exportNotes').hidden,true);assert.equal(h.get('openDiagnostics').hidden,true);
  assert.match(h.get('countHeading').textContent,/本页/);
  const count=h.requests.length;h.choose();
  assert.equal(h.get('detailTitle').textContent,'<img src=x onerror=alert(1)>');
  assert.equal(h.get('goalStatus').textContent,'目标待验证');
  assert.match(h.get('goalSummary').textContent,/仅显示最近 5 次/);
  h.get('localView').onclick();assert.equal(h.get('loadNeighbors').hidden,true);
  h.get('loadNeighbors').onclick();assert.equal(h.requests.length,count);
  h.inspect();assert.equal(h.get('relationKind').textContent,'目标证据引用');
  assert.match(h.get('relationWeight').textContent,/不适用/);
  assert.equal(h.get('readRelationSource').hidden,true);
  h.get('relationTo').onclick();assert.equal(h.get('goalStatus').textContent,'请求已处理');
  h.choose(2);assert.equal(h.get('goalStatus').textContent,'检查结果未知');
  assert.match(h.get('goalSummary').textContent,/artifact.txt/);
  assert.match(h.get('goalSummary').textContent,/观测 SHA-256：未取得/);
  assert.equal(h.requests.length,count);
  h.get('zoomIn').onclick();const zoom=h.get('zoomLevel').textContent;
  h.get('zoomOut').onclick();assert.notEqual(h.get('zoomLevel').textContent,zoom);
});

test('goal detail loads earlier checks through a read-only paged request',async()=>{
  const h=await loaded();goals(h);h.requests.at(-1).resolve(goalGraph());await flush();
  h.choose();assert.equal(h.get('loadGoalHistory').hidden,false);
  h.get('loadGoalHistory').onclick();const request=h.requests.at(-1);
  assert.match(request.url,/\/api\/goals\/g\/history\?limit=20&offset=5/);
  assert.equal(request.options.cache,'no-store');
  request.resolve({goal_id:'g',records:[{run_id:'run-2',outcome:'mismatch',observed_at:'2026-09-01',base_goal_version:2,committed_goal_version:3,files:[{path:'artifact.txt',outcome:'mismatch'}]}],total:7,offset:5,limit:20,next_offset:null,read_only:true});await flush();
  assert.equal(h.get('loadGoalHistory').hidden,true);
  assert.match(h.get('goalHistoryStatus').textContent,/历史已读完/);
  assert.match(h.get('goalHistoryList').children[0].children[0].children[1].textContent,/artifact.txt/);
  const before=h.requests.length;h.choose(1);assert.equal(h.get('goalHistoryList').children.length,0);assert.equal(h.requests.length,before);
});

test('goal actions are explicit, version-checked and refresh the evidence projection',async()=>{
  const h=await loaded();goals(h);const initial=goalGraph();initial.nodes[0].record.verification={kind:'workspace_files_sha256_v1',files:[{path:'artifact.txt',sha256:'a'.repeat(64)}]};h.requests.at(-1).resolve(initial);await flush();h.choose();
  assert.equal(h.get('verifyGoal').hidden,false);assert.equal(h.get('cancelGoal').hidden,false);
  h.get('verifyGoal').onclick();const action=h.requests.at(-1);
  assert.match(action.url,/\/api\/goals\/g\/verify/);assert.equal(action.options.method,'POST');
  assert.deepEqual(JSON.parse(action.options.body),{expected_version:3});
  assert.equal(h.get('verifyGoal').disabled,true);assert.equal(h.get('cancelGoal').disabled,true);
  action.resolve({goal_id:'g',status:'completed',version:4});await flush();
  const refresh=h.requests.at(-1);assert.match(refresh.url,/\/api\/goals\/graph/);refresh.resolve((()=>{const d=goalGraph();d.nodes[0]={...d.nodes[0],status:'completed',record:{...d.nodes[0].record,status:'completed',version:4}};return d;})());await flush();
  assert.equal(h.get('verifyGoal').hidden,true);assert.equal(h.get('cancelGoal').hidden,true);assert.match(h.get('goalStatus').textContent,/指定条件已通过/);
});

test('mode switches fence stale memory, detail and goal responses and recover from disabled goals',async()=>{
  const h=await loaded();h.choose();const detail=h.requests.at(-1);
  h.get('reloadGraph').onclick();const memory=h.requests.at(-1);
  goals(h);assert.equal(detail.options.signal.aborted,true);assert.equal(memory.options.signal.aborted,true);
  const unavailable=h.requests.at(-1);unavailable.reject(new Error('business goals are not enabled'));await flush();
  assert.match(h.get('graphMessage').textContent,/目标服务可能未启用/);
  assert.equal(h.get('listCount').textContent,0);assert.equal(h.get('nextGoals').disabled,true);
  memory.resolve(graph());detail.resolve({...world,label:'stale'});await flush();assert.equal(h.get('listCount').textContent,0);
  h.get('reloadGraph').onclick();const lateGoal=h.requests.at(-1);
  h.get('graphSource').value='memory';h.get('graphSource').onchange();
  h.requests.at(-1).resolve(graph());lateGoal.resolve(goalGraph());await flush();
  assert.equal(h.get('listCount').textContent,2);assert.equal(h.get('goalPaging').hidden,true);
  assert.equal(h.get('exportNotes').hidden,false);assert.equal(h.get('openDiagnostics').hidden,false);
  h.choose();assert.equal(h.get('goalDetail').hidden,true);assert.match(h.requests.at(-1).url,/\/api\/memory\/graph\/node/);
});

test('goal pagination failures retain the displayed page and search resets the offset',async()=>{
  const h=await loaded();goals(h);h.requests.at(-1).resolve(goalGraph(0,40));await flush();
  assert.equal(h.get('goalPageStatus').textContent,'1 / 2 页');
  h.get('nextGoals').onclick();assert.match(h.requests.at(-1).url,/offset=40/);
  assert.equal(h.get('nextGoals').disabled,true);
  h.requests.at(-1).reject(new Error('offline'));await flush();
  assert.equal(h.get('goalPageStatus').textContent,'1 / 2 页');assert.equal(h.get('listCount').textContent,3);
  h.get('nextGoals').onclick();h.requests.at(-1).resolve(goalGraph(40));await flush();
  assert.equal(h.get('goalPageStatus').textContent,'2 / 2 页');assert.equal(h.get('nextGoals').disabled,true);
  h.get('graphSearch').value='target';h.get('graphSearch').events.input();h.timer(350);
  assert.match(h.requests.at(-1).url,/offset=0&q=target/);
});

async function linked(width=1200,dimension='2d',layout=null) {
  const h=harness(width,null,layout,dimension),far={...world,id:'S4:far',record_id:'far',label:'Far'};
  const data=graph([world,other,far]);data.edges=[
    {source:world.id,target:other.id,relation:'owns',kind:'stored_relation'},
    {source:other.id,target:far.id,relation:'uses',kind:'stored_relation'},
  ];
  h.requests[0].resolve(data);await flush();h.get('localDepth').value='1';
  return {h,data};
}

test('relation name selection synchronizes visible nodes, details and paths; reset preserves depth',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  const owns=JSON.stringify(['stored_relation','owns']);h.get('localRelation').value=owns;h.get('localRelation').onchange();
  assert.equal(h.get('listCount').textContent,2);assert.equal(h.get('detailTitle').textContent,'World');
  assert.match(h.get('localPathSummary').textContent,/所选节点就是当前中心/);assert.equal(h.get('neighborCount').textContent,1);
  h.choose(1);assert.match(h.get('localPathSummary').textContent,/1 步连接/);
  h.get('localDirection').value='incoming';h.get('localDirection').onchange();assert.equal(h.get('listCount').textContent,1);
  assert.equal(h.get('localRelation').value,owns);
  assert.match(h.get('localRelation').children[0].children.find(c=>c.value===owns).textContent,/（0）/);
  h.get('resetLocalFilters').onclick();assert.equal(h.get('localDepth').value,'2');assert.equal(h.get('listCount').textContent,3);
  assert.equal(h.get('localRelation').value,'');assert.equal(h.get('resetLocalFilters').disabled,true);
});

test('refresh keeps an unavailable selected relation at zero instead of silently broadening the graph',async()=>{
  const {h,data}=await linked();h.choose();h.get('localView').onclick();
  const owns=JSON.stringify(['stored_relation','owns']);h.get('localRelation').value=owns;h.get('localRelation').onchange();
  h.get('reloadGraph').onclick();h.requests.at(-1).resolve({...data,edges:[{...data.edges[0],relation:'uses'}]});await flush();
  assert.equal(h.get('localRelation').value,owns);assert.equal(h.get('listCount').textContent,1);
  assert.match(h.get('localRelation').children[0].children.find(c=>c.value===owns).textContent,/（0）/);
  assert.match(h.get('relationFilterNotice').textContent,/0 不代表记忆库中不存在/);
  h.get('resetLocalFilters').onclick();assert.equal(h.get('listCount').textContent,2);
});

test('neighbor pages add relation options while preserving the current exact filter and pagination',async()=>{
  const {h,data}=await linked();h.choose();h.get('localView').onclick();h.get('loadNeighbors').onclick();
  const request=h.requests.at(-1),owns=JSON.stringify(['stored_relation','owns']);
  h.get('localRelation').value=owns;h.get('localRelation').onchange();
  assert.equal(request.options.signal.aborted,false);
  request.resolve({center:world.id,nodes:data.nodes,edges:[{source:world.id,target:data.nodes[2].id,kind:'stored_relation',relation:'<literal>'}],next_offset:50,scope:{}});await flush();
  assert.equal(h.get('localRelation').value,owns);assert.equal(h.get('listCount').textContent,2);
  assert(h.get('localRelation').children[0].children.some(c=>c.textContent.includes('<literal>')));
  assert.equal(h.get('loadNeighbors').textContent,'继续加载邻居');
  h.get('resetLocalFilters').onclick();assert.equal(h.get('listCount').textContent,3);
});

test('a local two-step path opens exact relationship details and node navigation keeps its center',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  assert.equal(h.get('localPath').hidden,false);assert.match(h.get('localPathSummary').textContent,/2 步连接/);
  h.get('fitLocalPath').onclick();h.frame(10000);
  assert.equal(h.get('detailTitle').textContent,'Far');assert.equal(h.get('localCenterLabel').textContent,'World');
  // All three route nodes fit inside the 900 x 700 test canvas after framing.
  const dots=h.arcs.filter(a=>a[2]>2&&a[2]<7);
  assert(dots.length>=3);assert(dots.every(a=>a[0]>=40&&a[0]<=860&&a[1]>=40&&a[1]<=660));
  const steps=h.get('localPathSteps').children[0].children;
  assert.equal(steps[0].textContent,'World');assert.match(steps[1].children[0].textContent,/↓ owns · 顺向/);
  steps[2].children[0].events.click();assert.equal(h.get('relationName').textContent,'uses');
  assert.equal(h.get('relationFrom').textContent,'Other');assert.equal(h.get('relationTo').textContent,'Far');
  h.get('closeRelationInspector').onclick();steps[1].children[1].events.click();
  assert.equal(h.get('detailTitle').textContent,'Other');assert.equal(h.get('localCenterLabel').textContent,'World');
  assert.match(h.get('localPathSummary').textContent,/1 步连接/);
  h.get('globalView').onclick();assert.equal(h.get('localPath').hidden,true);assert.equal(h.get('localPathSteps').children.length,0);
});

test('reverse path arrows preserve relationship direction and center selection needs no path',async()=>{
  const {h}=await linked();h.choose(2);h.get('localView').onclick();
  assert.match(h.get('localPathSummary').textContent,/所选节点就是当前中心/);
  assert.equal(h.get('fitLocalPath').disabled,true);
  h.get('localDirection').value='incoming';h.get('localDirection').onchange();h.get('expandLocal').onclick();h.choose(0);
  assert.match(h.get('localPathSummary').textContent,/2 步连接/);
  const steps=h.get('localPathSteps').children[0].children;
  assert.match(steps[1].children[0].textContent,/↑ uses · 逆向/);
  steps[1].children[0].events.click();assert.equal(h.get('relationFrom').textContent,'Other');assert.equal(h.get('relationTo').textContent,'Far');
});

test('filtering and refresh discard paths that are no longer in the visible graph',async()=>{
  const {h,data}=await linked();h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  h.get('localDepth').value='1';h.get('localDepth').onchange();
  assert.equal(h.get('detailTitle').textContent,'World');assert.match(h.get('localPathSummary').textContent,/所选节点就是当前中心/);
  h.get('expandLocal').onclick();h.choose(2);h.get('reloadGraph').onclick();
  h.requests.at(-1).resolve({...data,edges:data.edges.slice(0,1)});await flush();
  assert.match(h.get('localPathSummary').textContent,/所选节点就是当前中心/);assert.equal(h.get('localPathSteps').children.length,0);
});

test('relation details show stored direction and fields without treating weight as confidence',async()=>{
  const {h}=await linked();h.choose();const requests=h.requests.length;h.inspect();
  assert.equal(h.get('relationInspector').open,true);assert.equal(h.get('relationName').textContent,'owns');
  assert.equal(h.get('relationFrom').textContent,'World');assert.equal(h.get('relationTo').textContent,'Other');
  assert.equal(h.get('relationWeight').textContent,'未记录');assert.equal(h.get('readRelationSource').hidden,true);
  assert.match(h.get('relationExplanation').textContent,/权重不是可信概率/);
  assert.equal(h.requests.length,requests);assert.equal(h.raf.size,0);
  h.get('closeRelationInspector').onclick();assert.equal(h.get('relationInspector').open,false);assert.equal(h.raf.size,1);
});
test('relation endpoints navigate to the exact node while retaining the local center',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.inspect();h.get('relationTo').onclick();
  assert.equal(h.get('relationInspector').open,false);assert.equal(h.get('detailTitle').textContent,'Other');
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.match(h.requests.at(-1).url,/record_id=other/);
});
test('source reads use the edge reference and cannot update a reopened relation after close',async()=>{
  const h=harness(),data=graph();data.edges=[{source:world.id,target:other.id,kind:'stored_relation',relation:'<script>literal</script>',weight:.7,
    provenance:{epistemic_status:'hypothesis',source_event_id:'outside/&event'}}];
  h.requests[0].resolve(data);await flush();h.choose();h.inspect();
  assert.equal(h.get('relationName').textContent,'<script>literal</script>');assert.equal(h.get('relationOrigin').textContent,'假设');
  assert.equal(h.get('relationWeight').textContent,'0.7');h.get('readRelationSource').onclick();
  const old=h.requests.at(-1);assert.match(old.url,/tier=S5&record_id=outside%2F%26event/);
  h.get('closeRelationInspector').onclick();assert.equal(old.options.signal.aborted,true);h.inspect();
  old.resolve({record_id:'outside/&event',content:'late old content'});await flush();assert.equal(h.get('relationSourceRecord').hidden,true);
  h.get('readRelationSource').onclick();h.requests.at(-1).resolve({record_id:'outside/&event',content:'<img onerror=alert(1)>',content_truncated:true});await flush();
  assert.equal(h.get('relationSourceBody').textContent,'<img onerror=alert(1)>');assert.match(h.get('relationSourceStatus').textContent,/内容已截断/);
});
test('source failures and timeouts stay retryable and do not claim a missing source was verified',async()=>{
  const h=harness(),event={...other,tier:'S5',id:'S5:event',record_id:'event'},data=graph([world,event]);
  data.edges=[{source:world.id,target:event.id,kind:'provenance',relation:'source_event',weight:.5}];
  h.requests[0].resolve(data);await flush();h.choose();h.inspect();
  assert.equal(h.get('relationWeight').textContent,'不适用（来源引用）');h.get('readRelationSource').onclick();
  h.requests.at(-1).reject(new Error('HTTP 404'));await flush();assert.match(h.get('relationSourceStatus').textContent,/未能验证来源内容/);
  assert.equal(h.get('readRelationSource').disabled,false);h.get('readRelationSource').onclick();
  h.timer(15000);assert.equal(h.requests.at(-1).options.signal.aborted,true);
  h.requests.at(-1).reject(Object.assign(new Error('timeout'),{name:'AbortError'}));await flush();
  assert.match(h.get('relationSourceStatus').textContent,/读取超时/);assert.equal(h.get('readRelationSource').disabled,false);
});
test('a relationship omitted by refresh closes its details without claiming database deletion',async()=>{
  const {h}=await linked();h.choose();h.inspect();h.get('reloadGraph').onclick();h.requests.at(-1).resolve(graph());await flush();
  assert.equal(h.get('relationInspector').open,false);assert.match(h.get('graphMessage').textContent,/不代表原始关系已删除/);
});
test('canvas relation clicks open details; dragging the same line pans instead',async()=>{
  const {h}=await linked();for(let i=1;i<=140;i++)h.frame(i*40);
  const {a,b}=h.lines[0],x=(a.x+b.x)/2,y=(a.y+b.y)/2,canvas=h.get('memoryCanvas');
  canvas.events.pointerdown({button:0,pointerId:1,clientX:x,clientY:y});canvas.events.pointerup({pointerId:1});
  assert.equal(h.get('relationInspector').open,true);h.get('closeRelationInspector').onclick();
  canvas.events.pointerdown({button:0,pointerId:2,clientX:x,clientY:y});
  canvas.events.pointermove({pointerId:2,clientX:x+30,clientY:y+20});canvas.events.pointerup({pointerId:2});
  assert.equal(h.get('relationInspector').open,false);
});

function pointer(canvas,type,id,x,y){canvas.events[type]({button:0,pointerId:id,pointerType:'touch',clientX:x,clientY:y});}
function assertPosition(actual,expected){assert.ok(Math.abs(actual[0]-expected[0])<1e-6);assert.ok(Math.abs(actual[1]-expected[1])<1e-6);}

async function galaxyLoaded(layout=null,width=1200,data=graph()){
  const h=harness(width,null,layout,'3d');h.requests[0].resolve(data);await flush();h.frame(100);return h;
}
const bodyPoint=(h,id=world.id)=>{const p=h.bodies.find(b=>b.node.id===id).projection;return [p.x,p.y];};
const spatialPositions=h=>Array.from(h.spatial().scene.positions,([id,p])=>[id,p.x,p.y,p.z]);

test('3D rotation changes projection without moving records or selecting the dragged star',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas'),before={...h.spatial().camera},positions=spatialPositions(h),point=bodyPoint(h);
  const [x,y]=bodyPoint(h);pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointermove',1,x+50,y+20);pointer(canvas,'pointerup',1,x+50,y+20);h.frame(200);
  assert.ok(Math.abs(h.spatial().camera.yaw-before.yaw-.4)<1e-9);assert.ok(Math.abs(h.spatial().camera.pitch-before.pitch-.12)<1e-9);
  assert.deepEqual(spatialPositions(h),positions);assert.notDeepEqual(bodyPoint(h),point);assert.equal(h.requests.length,1);
  assert.equal(h.get('detailContent').hidden,true);assert.equal(h.workspace.classList.contains('galaxy-view'),true);
});

test('3D pan mode and Shift drag translate the camera without changing orientation',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas'),before={...h.spatial().camera},point=bodyPoint(h);
  h.get('graphNavigation').value='pan';h.get('graphNavigation').onchange();
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointermove',1,140,125);pointer(canvas,'pointerup',1,140,125);h.frame(200);
  assertPosition(bodyPoint(h),[point[0]+40,point[1]+25]);assert.equal(h.spatial().camera.yaw,before.yaw);assert.equal(h.spatial().camera.pitch,before.pitch);
  h.get('graphNavigation').value='rotate';canvas.events.pointerdown({button:0,pointerId:2,clientX:100,clientY:100,shiftKey:true});
  pointer(canvas,'pointermove',2,125,100);pointer(canvas,'pointerup',2,125,100);h.frame(300);
  assertPosition(bodyPoint(h),[point[0]+65,point[1]+25]);assert.equal(h.spatial().camera.yaw,before.yaw);
});

test('3D pinch zoom anchors each depth plane and the remaining finger pans',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas'),before={...h.spatial().camera},points=new Map(h.bodies.map(b=>[b.node.id,[b.projection.x,b.projection.y]]));
  pointer(canvas,'pointerdown',1,200,250);pointer(canvas,'pointerdown',2,300,250);
  pointer(canvas,'pointermove',1,150,250);pointer(canvas,'pointermove',2,350,250);h.frame(200);
  assert.equal(h.spatial().camera.scale,before.scale*2);assert.equal(h.spatial().camera.yaw,before.yaw);
  for(const [id,p] of points)assertPosition(bodyPoint(h,id),[250+(p[0]-250)*2,250+(p[1]-250)*2]);
  const after=bodyPoint(h);pointer(canvas,'pointerup',2,350,250);pointer(canvas,'pointermove',1,180,270);h.frame(300);
  assertPosition(bodyPoint(h),[after[0]+30,after[1]+20]);assert.equal(h.spatial().camera.yaw,before.yaw);
  pointer(canvas,'pointerup',1,180,270);assert.equal(h.get('detailContent').hidden,true);assert.equal(h.requests.length,1);
});

test('3D star tap inspects its actual record; hierarchy follows filtered local paths',async()=>{
  const {h}=await linked(1200,'3d');h.frame(100);const canvas=h.get('memoryCanvas'),[x,y]=bodyPoint(h);
  pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointerup',1,x,y);assert.equal(h.get('detailTitle').textContent,'World');
  h.get('localView').onclick();h.get('expandLocal').onclick();h.frame(200);
  const positions=h.spatial().scene.positions;
  assert.equal(positions.get(world.id).role,'star');assert.equal(positions.get(other.id).role,'planet');assert.equal(positions.get('S4:far').role,'moon');assert.equal(positions.get('S4:far').parent,other.id);
  h.choose(2);assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('detailTitle').textContent,'Far');
  h.get('localRelation').value=JSON.stringify(['stored_relation','owns']);h.get('localRelation').onchange();h.frame(300);
  assert.equal(h.spatial().scene.positions.has('S4:far'),false);assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.get('localCenterLabel').textContent,'World');
});

const systemGraph=()=>graph([world,other,{...world,id:'S3:long',record_id:'long',tier:'S3',label:'Long'},{...other,id:'S3:second',record_id:'second',tier:'S3',label:'Second'}]);
function locate(h,id){h.get('galaxySystem').value=id;h.get('galaxySystem').onchange();}

test('system navigation frames loaded tier members without filtering, clearing selection or fetching',async()=>{
  const h=await galaxyLoaded(null,1280,systemGraph());h.choose();h.get('immersiveToggle').onclick();h.frame(200);
  const requests=h.requests.length,stats=h.get('graphStats').textContent,positions=spatialPositions(h);
  locate(h,'S3');h.frame(300);
  assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.requests.length,requests);assert.equal(h.get('graphStats').textContent,stats);
  assert.deepEqual(spatialPositions(h),positions);assert.equal(h.get('galaxySystemControl').hidden,false);
  assert.equal(h.get('immersiveScope').textContent,'长期记忆 · 2 个已加载节点');
  assert.ok(h.get('galaxySystem').children[0].children.some(option=>option.textContent==='S3 · 长期记忆 · 2'));
  for(const id of ['S3:long','S3:second']){const [x,y]=bodyPoint(h,id);assert.ok(x>=24&&x<=h.spatial().camera.width-24);assert.ok(y>=184&&y<=h.spatial().camera.height-110);}
  h.get('fitGraph').onclick();h.frame(400);assert.equal(h.get('galaxySystem').value,'S3');
  h.get('resetCamera').onclick();h.frame(500);assert.equal(h.get('galaxySystem').value,'');assert.match(h.get('immersiveScope').textContent,/全局图谱/);
});

test('system camera returns after local and dimensional navigation, but an explicit node focus leaves it',async()=>{
  const h=await galaxyLoaded(null,1280,systemGraph());h.choose(2);locate(h,'S3');h.frame(200);const before={...h.spatial().camera};
  h.get('localView').onclick();assert.equal(h.get('galaxySystemControl').hidden,true);
  h.get('globalView').onclick();h.frame(300);assert.equal(h.get('galaxySystem').value,'S3');
  for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],before[key]);
  h.get('graphDimension').value='2d';h.get('graphDimension').onchange();assert.equal(h.get('galaxySystemControl').hidden,true);
  h.get('graphDimension').value='3d';h.get('graphDimension').onchange();h.frame(400);assert.equal(h.get('galaxySystem').value,'S3');
  h.get('focusNode').onclick();h.frame(500);assert.equal(h.get('galaxySystem').value,'');assert.equal(h.get('detailTitle').textContent,'Long');
});

test('system removal falls back to the overview and navigation cancels input without acting under a modal',async()=>{
  const h=await galaxyLoaded(null,1280,systemGraph()),canvas=h.get('memoryCanvas');locate(h,'S3');h.frame(200);
  pointer(canvas,'pointerdown',1,120,180);locate(h,'S4');h.frame(300);assert.equal(h.captured.size,0);
  const before={...h.spatial().camera};h.dialog(true);locate(h,'S3');h.dialog(false);h.frame(400);
  assert.deepEqual({...h.spatial().camera},before);
  locate(h,'S3');h.get('reloadGraph').onclick();h.requests.at(-1).resolve(graph([world,other]));await flush();h.frame(500);
  assert.equal(h.get('galaxySystem').value,'');assert.equal(h.get('graphStats').textContent,'2 节点 · 0 连接');
  assert.ok(!h.get('galaxySystem').children[0].children.some(option=>option.value==='S3'));
  for(const id of [world.id,other.id]){const [x,y]=bodyPoint(h,id);assert.ok(x>=24&&x<=876);assert.ok(y>=24&&y<=620);}
});

test('phone system framing avoids the immersive top HUD and bottom controls',async()=>{
  const layout={canvas:{left:43,top:0,width:277,height:568},panel:{left:0,top:0,width:0,height:0}};
  const h=await galaxyLoaded(layout,320,systemGraph());h.get('immersiveToggle').onclick();locate(h,'S3');h.frame(200);
  for(const id of ['S3:long','S3:second']){const [x,y]=bodyPoint(h,id);assert.ok(x>=24&&x<=253);assert.ok(y>=172&&y<=398);}
  assert.equal(h.get('graphStats').textContent,'4 节点 · 0 连接');assert.equal(h.requests.length,1);
});

test('returning from local exploration refits when the saved system was filtered out',async()=>{
  const h=await galaxyLoaded(null,1280,systemGraph());h.choose();locate(h,'S3');h.get('localView').onclick();
  const s3=h.get('tierFilters').children.find(row=>row.children[2].textContent==='S3 长期记忆').children[0];
  s3.checked=false;s3.events.change();h.get('globalView').onclick();h.frame(300);
  assert.equal(h.get('galaxySystem').value,'');assert.equal(h.get('graphStats').textContent,'2 节点 · 0 连接');
  for(const id of [world.id,other.id]){const [x,y]=bodyPoint(h,id);assert.ok(x>=24&&x<=876);assert.ok(y>=24&&y<=620);}
});

async function flightGraph(motion={enabled:true}){
  const h=harness(1280,null,null,'3d',motion);h.requests[0].resolve(systemGraph());await flush();h.frame(100);return h;
}
const cameraValues=h=>Object.fromEntries(['scale','panX','panY','yaw','pitch'].map(key=>[key,h.spatial().camera[key]]));

test('smooth system navigation eases between real cameras and completes with particles disabled',async()=>{
  const h=await flightGraph(),instant=await flightGraph({enabled:false});
  h.get('animateGraph').checked=false;const before=cameraValues(h),positions=spatialPositions(h);
  locate(instant,'S3');instant.frame(200);const target=cameraValues(instant);
  locate(h,'S3');h.frame(200);assert.deepEqual(cameraValues(h),before);
  h.frame(305);const middle=cameraValues(h);
  for(const key of ['scale','panX','panY'])assert.ok(Math.abs(middle[key]-(before[key]+(target[key]-before[key])*.15625))<1e-9);
  assert.equal(h.raf.size,1);h.frame(620);assert.deepEqual(cameraValues(h),target);assert.equal(h.raf.size,0);
  assert.equal(h.get('galaxySystem').value,'S3');assert.deepEqual(spatialPositions(h),positions);assert.equal(h.requests.length,1);
});

test('a new camera destination starts at the current visible pose and replaces the old flight',async()=>{
  const h=await flightGraph(),instant=await flightGraph({enabled:false});locate(h,'S3');h.frame(200);h.frame(320);
  const middle=cameraValues(h);locate(h,'S4');h.frame(400);assert.deepEqual(cameraValues(h),middle);
  locate(instant,'S4');instant.frame(200);h.frame(820);assert.deepEqual(cameraValues(h),cameraValues(instant));
  h.frame(1000);assert.deepEqual(cameraValues(h),cameraValues(instant));assert.equal(h.get('galaxySystem').value,'S4');
});

test('direct input, overlay resize, refresh, visibility and modal state stop old camera movement',async()=>{
  for(const action of [
    h=>{const c=h.get('memoryCanvas');pointer(c,'pointerdown',1,100,200);pointer(c,'pointermove',1,130,215);pointer(c,'pointerup',1,130,215);},
    h=>h.get('zoomIn').onclick(),
    h=>h.get('memoryCanvas').events.keydown({key:'ArrowRight',preventDefault(){}}),
    h=>h.resize('memoryCanvas'),
    h=>h.get('reloadGraph').onclick(),
    h=>{h.visible(false);h.visible(true);},
    h=>{h.dialog(true);h.dialog(false);}
  ]){
    const h=await flightGraph();locate(h,'S3');h.frame(200);h.frame(320);action(h);h.frame(400);const stopped=cameraValues(h);
    h.frame(1000);assert.deepEqual(cameraValues(h),stopped);assert.equal(h.get('galaxySystem').value,'S3');
  }
});

test('reduced motion and disabling smooth cameras settle at the destination without resuming an old flight',async()=>{
  const instant=await flightGraph({enabled:false});locate(instant,'S3');instant.frame(200);const target=cameraValues(instant);
  const reduced=await flightGraph({enabled:true,reduced:true});assert.equal(reduced.get('cameraMotion').checked,false);assert.equal(reduced.get('cameraMotion').disabled,true);
  locate(reduced,'S3');reduced.frame(200);assert.deepEqual(cameraValues(reduced),target);
  for(const finish of [h=>h.reducedMotion(true),h=>{h.get('cameraMotion').checked=false;h.get('cameraMotion').oninput();}]){
    const h=await flightGraph();locate(h,'S3');h.frame(200);h.frame(320);finish(h);h.frame(400);assert.deepEqual(cameraValues(h),target);
    h.reducedMotion(false);h.frame(1000);assert.deepEqual(cameraValues(h),target);assert.equal(h.get('cameraMotion').checked,false);
  }
});

test('camera orientation flies along the shorter turn and finishes at the exact selected preset',async()=>{
  const h=await flightGraph(),canvas=h.get('memoryCanvas');
  for(let i=0;i<31;i++)canvas.events.keydown({key:'ArrowRight',preventDefault(){}});h.frame(200);const yaw=h.spatial().camera.yaw;
  assert.ok(yaw>Math.PI);h.get('cameraView').value='top';h.get('cameraView').onchange();h.frame(300);h.frame(510);
  assert.ok(h.spatial().camera.yaw>yaw);assert.equal(h.get('cameraView').value,'custom');
  h.frame(720);assert.equal(h.spatial().camera.yaw,0);assert.equal(h.spatial().camera.pitch,0);assert.equal(h.get('cameraView').value,'top');
});

test('smooth phone focus reaches the same safe endpoint and retains the local center and selected path',async()=>{
  const {h}=await linked(390,'3d',phoneLayout());h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);h.frame(100);
  const center=h.get('localCenterLabel').textContent,path=h.get('localPathSummary').textContent,requests=h.requests.length;
  h.get('cameraMotion').checked=true;h.get('focusNode').onclick();h.frame(200);h.frame(620);
  const [x,y]=bodyPoint(h,'S4:far');assert.ok(Math.abs(x-h.spatial().camera.width/2)<1e-9);assert.ok(y>=24&&y<=h.spatial().camera.height-80);
  assert.equal(h.get('localCenterLabel').textContent,center);assert.equal(h.get('localPathSummary').textContent,path);assert.equal(h.get('detailTitle').textContent,'Far');assert.equal(h.requests.length,requests);
});

test('3D hit testing chooses the nearer sphere when projections overlap',async()=>{
  const h=await galaxyLoaded(),scene=h.spatial().scene,canvas=h.get('memoryCanvas');
  scene.positions.set(world.id,{x:0,y:0,z:0,role:'star'});scene.positions.set(other.id,{x:0,y:0,z:100,role:'planet'});
  h.get('memoryCanvas').events.keydown({key:'Home',preventDefault(){}});h.frame(200);
  // Overlap both bodies through their shared projected ray using inverse rotation.
  const camera=h.spatial().camera,p=scene.positions.get(other.id),sin=Math.sin(camera.yaw),cos=Math.cos(camera.yaw);
  p.x=-sin*Math.cos(camera.pitch)*100;p.y=Math.sin(camera.pitch)*100;p.z=cos*Math.cos(camera.pitch)*100;
  h.get('showLabels').oninput();h.frame(300);const [x,y]=bodyPoint(h);
  assertPosition(bodyPoint(h,other.id),[x,y]);pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointerup',1,x,y);
  assert.equal(h.get('detailTitle').textContent,'Other');
});

test('3D refresh preserves local filters, orientation and zoom; global camera returns',async()=>{
  const {h,data}=await linked(1200,'3d');h.frame(100);const canvas=h.get('memoryCanvas');
  canvas.events.keydown({key:'ArrowRight',preventDefault(){}});h.get('zoomIn').onclick();h.choose();h.frame(200);const global={...h.spatial().camera};
  h.get('localView').onclick();h.get('localDirection').value='outgoing';h.get('localDirection').onchange();
  canvas.events.keydown({key:'ArrowDown',preventDefault(){}});h.get('zoomIn').onclick();h.frame(300);const local={...h.spatial().camera};
  h.get('reloadGraph').onclick();h.requests.at(-1).resolve(data);await flush();h.frame(400);
  for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],local[key]);assert.equal(h.get('localDirection').value,'outgoing');assert.equal(h.get('localCenterLabel').textContent,'World');
  h.get('globalView').onclick();h.frame(500);for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],global[key]);
});

test('switching 3D to 2D and back retains selected records, local center and each camera',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.get('localView').onclick();h.frame(100);
  h.get('memoryCanvas').events.keydown({key:'ArrowLeft',preventDefault(){}});h.get('zoomIn').onclick();h.frame(200);const before={...h.spatial().camera};
  h.get('graphDimension').value='2d';h.get('graphDimension').onchange();h.frame(300);
  assert.equal(h.workspace.classList.contains('galaxy-view'),false);assert.equal(h.get('graphNavigation').disabled,true);assert.equal(h.get('localCenterLabel').textContent,'World');
  h.get('zoomOut').onclick();h.get('graphDimension').value='3d';h.get('graphDimension').onchange();h.frame(400);
  for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],before[key]);assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.get('localCenterLabel').textContent,'World');
});

test('3D phone drawer and filter disclosure preserve orientation and keep selection visible',async()=>{
  const layout=phoneLayout(),h=await galaxyLoaded(layout,390,graph([world]));h.choose();h.get('localView').onclick();h.get('toggleLocalDetail').onclick();h.frame(100);
  const canvas=h.get('memoryCanvas');canvas.events.keydown({key:'ArrowRight',preventDefault(){}});h.get('zoomIn').onclick();h.frame(200);const before={...h.spatial().camera};
  const [x,y]=bodyPoint(h);assert.ok(x>=24&&x<=347-24);assert.ok(y>=24&&y<=311-80);assert.equal(h.spatial().clearHeight,690);
  h.get('localFilterPanel').open=true;layout.canvas.height=410;h.resize('memoryCanvas');h.get('localFilterPanel').events.toggle();h.frame(300);
  for(const key of ['yaw','pitch','scale'])assert.equal(h.spatial().camera[key],before[key]);assert.equal(h.get('localCenterLabel').textContent,'World');
  h.get('localFilterPanel').open=false;layout.canvas.height=690;h.get('localFilterPanel').events.toggle();h.resize('memoryCanvas');h.frame(400);
  h.get('toggleLocalDetail').onclick();h.frame(500);assert.equal(h.spatial().camera.height,690);assert.equal(h.spatial().camera.scale,before.scale);
});

test('3D reset and Home restore orientation and cancel stale captured gestures',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas');pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointermove',1,150,125);
  h.get('resetCamera').onclick();h.frame(200);assert.equal(h.captured.size,0);assert.equal(h.spatial().camera.yaw,-.45);assert.equal(h.spatial().camera.pitch,.55);
  canvas.events.keydown({key:'ArrowUp',preventDefault(){}});canvas.events.keydown({key:'Home',preventDefault(){}});h.frame(300);
  assert.equal(h.spatial().camera.yaw,-.45);assert.equal(h.spatial().camera.pitch,.55);
  pointer(canvas,'pointerdown',2,100,100);h.visible(false);assert.equal(h.captured.size,0);pointer(canvas,'pointerup',2,100,100);assert.equal(h.requests.length,1);
});

test('3D tier filtering repositions the selected star into the viewport without changing orientation or scale',async()=>{
  const long={...other,id:'S3:long',tier:'S3',label:'Long'},h=await galaxyLoaded(null,1200,graph([world,long]));h.choose(1);h.get('zoomIn').onclick();h.get('zoomIn').onclick();h.frame(200);
  const before={...h.spatial().camera},s4=h.get('tierFilters').children.find(row=>row.children[2].textContent==='S4 世界模型').children[0];s4.checked=false;s4.events.change();h.frame(300);
  const [x,y]=bodyPoint(h,long.id);assert.ok(x>=24&&x<=before.width-24);assert.ok(y>=24&&y<=before.height-80);assert.equal(h.get('detailTitle').textContent,'Long');
  for(const key of ['scale','yaw','pitch'])assert.equal(h.spatial().camera[key],before[key]);
});

test('3D global camera stays separate from local camera through dimension changes and exit in 2D',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.frame(100);const canvas=h.get('memoryCanvas');canvas.events.keydown({key:'ArrowRight',preventDefault(){}});h.frame(200);const global={...h.spatial().camera};
  const change=dimension=>{h.get('graphDimension').value=dimension;h.get('graphDimension').onchange();};
  change('2d');change('3d');h.get('localView').onclick();h.get('zoomIn').onclick();h.get('zoomIn').onclick();h.frame(300);assert.notEqual(h.spatial().camera.scale,global.scale);
  change('2d');h.get('globalView').onclick();change('3d');h.frame(400);
  for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],global[key]);assert.equal(h.get('localView').classList.contains('chosen'),false);
});

test('a new search clears old dimension cameras while a page refresh retains them',async()=>{
  const h=await galaxyLoaded();h.get('graphDimension').value='2d';h.get('graphDimension').onchange();h.get('zoomIn').onclick();h.get('zoomIn').onclick();
  const old=h.get('zoomLevel').textContent;h.get('graphDimension').value='3d';h.get('graphDimension').onchange();
  h.get('graphSearch').value='new';h.get('graphSearch').events.input();h.timer(350);h.requests.at(-1).resolve(graph([world],'new'));await flush();
  h.get('graphDimension').value='2d';h.get('graphDimension').onchange();assert.equal(h.get('zoomLevel').textContent,'150%');assert.notEqual(h.get('zoomLevel').textContent,old);
  h.get('zoomIn').onclick();const latest=h.get('zoomLevel').textContent;h.get('reloadGraph').onclick();h.requests.at(-1).resolve(graph([world],'new'));await flush();
  h.get('graphDimension').value='3d';h.get('graphDimension').onchange();h.get('graphDimension').value='2d';h.get('graphDimension').onchange();assert.equal(h.get('zoomLevel').textContent,latest);
});

test('camera presets reframe the same local records and preserve center, selection, filters and paths',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);h.get('localDirection').value='outgoing';h.get('localDirection').onchange();h.frame(100);
  const positions=spatialPositions(h),requests=h.requests.length,title=h.get('detailTitle').textContent,path=h.get('localPathSummary').textContent;
  let time=200;
  for(const [name,yaw,pitch] of [['top',0,0],['side',0,1.2],['oblique',-.45,.55]]){
    h.get('cameraView').value=name;h.get('cameraView').onchange();h.frame(time);time+=100;
    assert.equal(h.spatial().camera.yaw,yaw);assert.equal(h.spatial().camera.pitch,pitch);assert.equal(h.get('cameraView').value,name);
    assert.equal(h.get('detailTitle').textContent,title);assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('localDirection').value,'outgoing');assert.equal(h.get('localPathSummary').textContent,path);
    assert.deepEqual(spatialPositions(h),positions);assert.equal(h.requests.length,requests);
  }
});

test('immersive map mode keeps scope state and exits through HUD or Escape',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();
  h.get('immersiveToggle').onclick();assert.equal(h.workspace.classList.contains('immersive-map'),true);assert.equal(h.get('immersiveHud').hidden,false);assert.match(h.get('immersiveScope').textContent,/局部关系 · 3 节点/);
  h.get('immersiveReset').onclick();assert.equal(h.workspace.classList.contains('immersive-map'),true);
  h.get('immersiveExit').onclick();assert.equal(h.workspace.classList.contains('immersive-map'),false);
  h.get('immersiveToggle').onclick();h.key({key:'Escape'});assert.equal(h.workspace.classList.contains('immersive-map'),false);
});

test('immersive detail collapse preserves selection, local path and zoom through a late detail read',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  h.get('immersiveToggle').onclick();h.frame(100);
  const requests=h.requests.length,title=h.get('detailTitle').textContent,path=h.get('localPathSummary').textContent,zoom=h.get('zoomLevel').textContent;
  assert.equal(h.get('immersiveDetails').disabled,false);
  h.get('closeImmersiveDetail').onclick();assert.equal(h.workspace.classList.contains('has-selection'),false);
  h.requests.at(-1).resolve({...world,id:'S4:far',record_id:'far',label:title,content:'late full record'});await flush();
  assert.equal(h.workspace.classList.contains('has-selection'),false);assert.equal(h.get('detailBody').textContent,'late full record');
  h.get('immersiveDetails').onclick();assert.equal(h.workspace.classList.contains('has-selection'),true);
  assert.equal(h.get('detailTitle').textContent,title);assert.equal(h.get('localCenterLabel').textContent,'World');
  assert.equal(h.get('localPathSummary').textContent,path);assert.equal(h.get('zoomLevel').textContent,zoom);assert.equal(h.requests.length,requests);
  h.get('immersiveRelations').onclick();assert.equal(h.workspace.classList.contains('immersive-local-open'),true);
  h.get('globalView').onclick();assert.equal(h.workspace.classList.contains('immersive-local-open'),false);assert.equal(h.get('immersiveRelations').hidden,true);
});

test('immersive desktop detail avoids its overlay and directory navigation returns to the regular map',async()=>{
  const layout={canvas:{left:52,top:0,width:900,height:700},panel:{left:650,top:80,width:300,height:470}};
  const h=await galaxyLoaded(layout,1280,graph([world]));h.choose();h.get('immersiveToggle').onclick();h.frame(200);
  const zoom=h.get('zoomLevel').textContent,requests=h.requests.length;
  assert.equal(h.spatial().camera.width,598);assert.equal(h.workspace.style.values['--graph-view-right'],'302px');
  h.get('closeImmersiveDetail').onclick();h.frame(300);assert.equal(h.spatial().camera.width,900);assert.equal(h.get('zoomLevel').textContent,zoom);
  h.get('immersiveDetails').onclick();h.frame(400);assert.equal(h.spatial().camera.width,598);
  h.get('explorerToggle').onclick();assert.equal(h.workspace.classList.contains('immersive-map'),false);assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.requests.length,requests);
});

test('phone immersive navigation and details share one drawer and leave the selected record above it',async()=>{
  const layout={canvas:{left:43,top:0,width:347,height:844},panel:{left:51,top:410,width:331,height:279}};
  const data=graph();data.edges=[{source:world.id,target:other.id,kind:'stored_relation',relation:'related_to'}];
  const h=await galaxyLoaded(layout,390,data);h.get('localControls').getBoundingClientRect=()=>({left:51,top:390,width:331,height:299});
  h.choose();h.get('localView').onclick();h.get('immersiveToggle').onclick();h.get('immersiveRelations').onclick();h.frame(200);
  assert.equal(h.workspace.classList.contains('has-selection'),false);assert.equal(h.spatial().camera.height,390);
  h.get('immersiveDetails').onclick();h.frame(300);assert.equal(h.workspace.classList.contains('immersive-local-open'),false);assert.equal(h.spatial().camera.height,410);
  h.get('immersiveRelations').onclick();h.choose(1);h.frame(400);
  assert.equal(h.workspace.classList.contains('immersive-local-open'),false);assert.equal(h.workspace.classList.contains('has-selection'),true);
  assert.equal(h.get('detailTitle').textContent,'Other');assert.equal(h.get('localCenterLabel').textContent,'World');
  const [x,y]=bodyPoint(h,other.id);assert(x>=24&&x<=323&&y>=172&&y<=386);
});

test('manual rotation marks a free camera; Home and dimension restoration keep the preset selector truthful',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas');assert.equal(h.get('cameraView').value,'oblique');
  canvas.events.keydown({key:'ArrowRight',preventDefault(){}});h.frame(200);assert.equal(h.get('cameraView').value,'custom');const yaw=h.spatial().camera.yaw;
  h.get('graphDimension').value='2d';h.get('graphDimension').onchange();assert.equal(h.get('cameraView').disabled,true);assert.equal(h.get('focusNode').hidden,true);
  h.get('graphDimension').value='3d';h.get('graphDimension').onchange();h.frame(300);assert.equal(h.get('cameraView').value,'custom');assert.equal(h.spatial().camera.yaw,yaw);
  canvas.events.keydown({key:'Home',preventDefault(){}});assert.equal(h.get('cameraView').value,'oblique');
});

test('focus enlarges and centers the selected satellite without changing its center, direction or records',async()=>{
  const {h}=await linked(1200,'3d');h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  h.get('memoryCanvas').events.keydown({key:'ArrowUp',preventDefault(){}});h.frame(100);
  const before={...h.spatial().camera},positions=spatialPositions(h),requests=h.requests.length;
  h.get('focusNode').onclick();h.frame(200);assertPosition(bodyPoint(h,'S4:far'),[before.width/2,before.height/2]);
  assert(h.spatial().camera.scale>=1.5);assert(h.spatial().camera.scale>=before.scale);assert.equal(h.spatial().camera.yaw,before.yaw);assert.equal(h.spatial().camera.pitch,before.pitch);
  assert.equal(h.get('detailTitle').textContent,'Far');assert.equal(h.get('localCenterLabel').textContent,'World');assert.match(h.get('localPathSummary').textContent,/2 步连接/);assert.deepEqual(spatialPositions(h),positions);assert.equal(h.requests.length,requests);
  for(let i=0;i<8;i++)h.get('zoomIn').onclick();h.get('focusNode').onclick();h.frame(300);assert.equal(h.spatial().camera.scale,4);
});

test('phone focus keeps the actual selected star above the drawer and does not open collapsed filters',async()=>{
  const h=await galaxyLoaded(phoneLayout(),390,graph([world]));h.choose();h.get('localView').onclick();h.get('toggleLocalDetail').onclick();h.frame(100);
  const requests=h.requests.length;h.get('focusNode').onclick();h.frame(200);const [x,y]=bodyPoint(h);
  assert.equal(x,h.spatial().camera.width/2);assert(y>=24&&y<=h.spatial().camera.height-80);assert.equal(h.get('localFilterPanel').open,false);assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.requests.length,requests);
});

test('view and focus commands cancel captured gestures and cannot act on empty selection or a modal',async()=>{
  const h=await galaxyLoaded(),canvas=h.get('memoryCanvas');assert.equal(h.get('focusNode').disabled,true);h.get('focusNode').onclick();assert.equal(h.requests.length,1);
  pointer(canvas,'pointerdown',1,100,100);h.get('cameraView').value='top';h.get('cameraView').onchange();h.frame(200);assert.equal(h.captured.size,0);assert.equal(h.spatial().camera.pitch,0);
  h.choose();pointer(canvas,'pointerdown',2,100,100);h.get('focusNode').onclick();h.frame(300);assert.equal(h.captured.size,0);const before={...h.spatial().camera};
  h.dialog(true);h.get('cameraView').value='side';h.get('cameraView').onchange();h.get('focusNode').onclick();h.dialog(false);h.frame(400);
  for(const key of ['yaw','pitch','scale','panX','panY'])assert.equal(h.spatial().camera[key],before[key]);
  h.get('clearSelection').onclick();assert.equal(h.get('focusNode').disabled,true);
});

test('pinching over a node zooms around the midpoint without moving the node or selecting it',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),[x,y]=h.arcs[0],before=Number.parseInt(h.get('zoomLevel').textContent);
  pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointerdown',2,x+100,y);
  pointer(canvas,'pointermove',1,x-50,y);pointer(canvas,'pointermove',2,x+150,y);h.frame(5700);
  assert.equal(Number.parseInt(h.get('zoomLevel').textContent),before*2);
  assertPosition(h.arcs[0],[x-50,y]);
  pointer(canvas,'pointerup',1,x-50,y);pointer(canvas,'pointerup',2,x+150,y);
  assert.equal(h.requests.length,1);assert.equal(h.get('detailContent').hidden,true);
});

test('two-finger translation pans the graph while keeping its scale',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),before=h.arcs.map(a=>a.slice(0,2)),zoom=h.get('zoomLevel').textContent;
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointerdown',2,200,100);
  pointer(canvas,'pointermove',1,130,120);pointer(canvas,'pointermove',2,230,120);h.frame(5700);
  assert.equal(h.get('zoomLevel').textContent,zoom);
  h.arcs.forEach((a,i)=>assertPosition(a,[before[i][0]+30,before[i][1]+20]));
  pointer(canvas,'pointerup',1,130,120);pointer(canvas,'pointerup',2,230,120);assert.equal(h.requests.length,1);
});

test('after a pinch, the remaining finger pans without resuming a node drag or tap',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas');h.choose();h.frame(5700);
  const [x,y]=h.arcs[0],before=h.arcs.map(a=>a.slice(0,2)),requests=h.requests.length,title=h.get('detailTitle').textContent;
  pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointerdown',2,x+100,y);
  pointer(canvas,'pointerup',2,x+100,y);pointer(canvas,'pointermove',1,x+30,y+20);h.frame(5800);
  h.arcs.forEach((a,i)=>assertPosition(a,[before[i][0]+30,before[i][1]+20]));
  pointer(canvas,'pointerup',1,x+30,y+20);
  assert.equal(h.requests.length,requests);assert.equal(h.get('detailTitle').textContent,title);
  assert.equal(h.get('detailContent').hidden,false);
});

for(const ending of ['pointercancel','lostpointercapture'])test(`${ending} abandons a gesture and a later single tap still selects`,async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),[x,y]=h.arcs[0];
  pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointerdown',2,x+100,y);
  pointer(canvas,ending,1,x,y);pointer(canvas,ending,2,x+100,y);
  pointer(canvas,'pointerup',1,x,y);pointer(canvas,'pointerup',2,x+100,y);assert.equal(h.requests.length,1);
  pointer(canvas,'pointerdown',3,x,y);pointer(canvas,'pointerup',3,x,y);
  assert.equal(h.requests.length,2);assert.equal(h.get('detailContent').hidden,false);
});

test('untracked and third pointers cannot change the tracked pair or finish its gesture',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),before=h.arcs.map(a=>a.slice(0,2)),zoom=h.get('zoomLevel').textContent;
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointerdown',2,200,100);
  pointer(canvas,'pointerdown',3,450,350);pointer(canvas,'pointermove',3,850,650);
  pointer(canvas,'pointerup',3,850,650);pointer(canvas,'pointerup',99,0,0);h.frame(5700);
  assert.equal(h.get('zoomLevel').textContent,zoom);h.arcs.forEach((a,i)=>assertPosition(a,before[i]));
  pointer(canvas,'pointermove',2,300,100);assert.notEqual(h.get('zoomLevel').textContent,zoom);
});

test('coincident touches and scale limits keep finite coordinates and allow immediate reversal',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas');
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointerdown',2,100,100);
  pointer(canvas,'pointermove',2,200,100);pointer(canvas,'pointermove',2,5000,100);
  assert.equal(h.get('zoomLevel').textContent,'400%');
  pointer(canvas,'pointermove',2,2550,100);assert.equal(h.get('zoomLevel').textContent,'200%');
  pointer(canvas,'pointermove',2,101,100);assert.equal(h.get('zoomLevel').textContent,'15%');h.frame(5700);
  assert.ok(h.arcs.every(a=>Number.isFinite(a[0])&&Number.isFinite(a[1])));
});

test('hiding the page cancels captured input without clicking, then a fresh tap works',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),[x,y]=h.arcs[0];
  pointer(canvas,'pointerdown',1,x,y);h.visible(false);pointer(canvas,'pointerup',1,x,y);
  assert.equal(h.requests.length,1);assert.equal(h.captured.size,0);h.visible(true);
  pointer(canvas,'pointerdown',2,x,y);pointer(canvas,'pointerup',2,x,y);assert.equal(h.requests.length,2);
});

test('single pointer node dragging still moves the node without activating a click',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas'),[x,y]=h.arcs[0];
  pointer(canvas,'pointerdown',1,x,y);pointer(canvas,'pointermove',1,x+30,y+20);h.frame(5700);
  assertPosition(h.arcs[0],[x+30,y+20]);pointer(canvas,'pointerup',1,x+30,y+20);
  assert.equal(h.requests.length,1);assert.equal(h.captured.size,0);
});

test('a view change cancels a pinch without clearing the existing local center or selection',async()=>{
  const h=await loaded(),canvas=h.get('memoryCanvas');h.choose();h.get('localView').onclick();
  const title=h.get('detailTitle').textContent,center=h.get('localCenterLabel').textContent,requests=h.requests.length;
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointerdown',2,200,100);
  h.get('showOrigins').checked=false;h.get('showOrigins').onchange();assert.equal(h.captured.size,0);
  pointer(canvas,'pointerup',1,100,100);pointer(canvas,'pointerup',2,200,100);
  assert.equal(h.get('detailTitle').textContent,title);assert.equal(h.get('detailContent').hidden,false);
  assert.equal(h.get('localCenterLabel').textContent,center);assert.equal(h.requests.length,requests);
});

test('local inspection holds its anchor; expansion, recenter and back preserve exploration',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('listCount').textContent,2);
  h.choose(1);assert.equal(h.get('detailTitle').textContent,'Other');
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('recenterLocal').disabled,false);
  h.get('expandLocal').onclick();assert.equal(h.get('listCount').textContent,3);
  assert.match(h.get('localSummary').textContent,/1 个直接邻居 · 1 个间接邻居/);
  h.get('recenterLocal').onclick();assert.equal(h.get('localCenterLabel').textContent,'Other');
  assert.equal(h.get('localBack').disabled,false);
  h.get('localBack').onclick();assert.equal(h.get('localCenterLabel').textContent,'World');
  assert.equal(h.get('localBack').disabled,true);
  h.get('expandLocal').onclick();assert.equal(h.get('localDepth').value,'3');assert.equal(h.get('expandLocal').disabled,true);
});

test('local breadcrumbs jump to any visited center and keep the remaining exploration history',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(1);h.get('recenterLocal').onclick();
  h.get('expandLocal').onclick();h.choose(2);h.get('recenterLocal').onclick();
  const crumbs=h.get('localBreadcrumbs').children[0].children;assert.equal(crumbs[0].textContent,'World');assert.equal(crumbs[2].textContent,'Other');assert.equal(crumbs[4].textContent,'Far');
  crumbs[0].events.click();
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('localBack').disabled,true);
  assert.equal(h.get('localBreadcrumbs').children[0].children.length,1);assert.equal(h.get('localBreadcrumbs').children[0].children[0].textContent,'World');
});

test('local filters restore the anchor when the inspected neighbor is hidden; global camera returns',async()=>{
  const {h}=await linked();h.choose();h.get('zoomIn').onclick();const zoom=h.get('zoomLevel').textContent;
  h.get('localView').onclick();h.choose(1);
  h.get('localDirection').value='incoming';h.get('localDirection').onchange();
  assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.get('listCount').textContent,1);
  assert.equal(h.get('localView').classList.contains('chosen'),true);
  h.get('showOrphans').checked=false;h.get('showOrphans').onchange();
  assert.equal(h.get('listCount').textContent,1);assert.equal(h.get('detailTitle').textContent,'World');
  h.get('globalView').onclick();assert.equal(h.get('localControls').hidden,true);
  assert.equal(h.get('zoomLevel').textContent,zoom);
});

test('refresh preserves a distinct local anchor, chosen depth and navigation history',async()=>{
  const {h,data}=await linked();h.choose();h.get('localView').onclick();h.choose(1);h.get('recenterLocal').onclick();
  h.get('expandLocal').onclick();h.choose(0);h.get('reloadGraph').onclick();
  h.requests.at(-1).resolve(data);await flush();
  assert.equal(h.get('localCenterLabel').textContent,'Other');assert.equal(h.get('detailTitle').textContent,'World');
  assert.equal(h.get('localDepth').value,'2');assert.equal(h.get('localBack').disabled,false);
  h.get('localBack').onclick();assert.equal(h.get('localCenterLabel').textContent,'World');
});
test('an omitted anchor returns to global while keeping an inspected node still in the projection',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.choose(1);
  h.get('reloadGraph').onclick();h.requests.at(-1).resolve(graph([other]));await flush();
  assert.equal(h.get('localControls').hidden,true);assert.equal(h.get('detailTitle').textContent,'Other');
  assert.equal(h.get('detailContent').hidden,false);assert.match(h.get('graphRefreshStatus').textContent,/原中心.*返回全局/);
});
test('narrow-screen local mode can hide details without losing the center or reopening on late reads',async()=>{
  const {h}=await linked(390);h.choose();const detail=h.requests[1];h.get('localView').onclick();
  assert.equal(h.workspace.classList.contains('has-selection'),false);
  detail.resolve({...world,content:'complete body'});await flush();
  assert.equal(h.workspace.classList.contains('has-selection'),false);
  h.get('toggleLocalDetail').onclick();assert.equal(h.workspace.classList.contains('has-selection'),true);
  h.get('toggleLocalDetail').onclick();assert.equal(h.workspace.classList.contains('has-selection'),false);
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('localControls').hidden,false);
});

function neighborPage(center=world.id,next=null){
  const added={...world,id:'S4:remote',record_id:'remote',label:'Remote neighbor'};
  return {center,nodes:[world,added],edges:[{source:world.id,target:added.id,kind:'stored_relation',relation:'references'}],next_offset:next,scope:{incomplete:false}};
}
test('database neighbors add unseen nodes, deduplicate pages and stop after the terminal page',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.get('loadNeighbors').onclick();
  assert.match(h.requests.at(-1).url,/neighbors\?.*offset=0/);assert.equal(h.get('loadNeighbors').disabled,true);
  h.requests.at(-1).resolve(neighborPage(world.id,50));await flush();
  assert.equal(h.get('listCount').textContent,3);assert.equal(h.get('localCenterLabel').textContent,'World');
  assert.equal(h.get('loadNeighbors').textContent,'继续加载邻居');
  h.get('loadNeighbors').onclick();assert.match(h.requests.at(-1).url,/offset=50/);
  h.requests.at(-1).resolve(neighborPage());await flush();
  assert.equal(h.get('listCount').textContent,3);assert.equal(h.get('loadNeighbors').disabled,true);
  assert.match(h.get('neighborReadStatus').textContent,/新增 0 个节点、0 条连接/);
});
test('late neighbor reads cannot overwrite another center or refreshed graph',async()=>{
  const {h,data}=await linked();h.choose();h.get('localView').onclick();h.get('loadNeighbors').onclick();const old=h.requests.at(-1);
  h.choose(1);h.get('recenterLocal').onclick();assert.equal(old.options.signal.aborted,true);
  old.resolve(neighborPage());await flush();assert.equal(h.get('localCenterLabel').textContent,'Other');
  assert.equal(h.get('listCount').textContent,3);
  h.get('loadNeighbors').onclick();const duringRefresh=h.requests.at(-1);
  h.get('reloadGraph').onclick();assert.equal(duringRefresh.options.signal.aborted,true);
  h.requests.at(-1).resolve(data);await flush();duringRefresh.resolve(neighborPage(other.id));await flush();
  assert.equal(h.get('listCount').textContent,3);
});
test('neighbor failures retain data and retry the same page, while incomplete results stay explicit',async()=>{
  const {h}=await linked();h.choose();h.get('localView').onclick();h.get('loadNeighbors').onclick();
  h.requests.at(-1).reject(new Error('HTTP 503'));await flush();
  assert.equal(h.get('listCount').textContent,2);assert.equal(h.get('loadNeighbors').disabled,false);
  assert.match(h.get('neighborReadStatus').textContent,/保留已有图谱/);
  h.get('loadNeighbors').onclick();assert.match(h.requests.at(-1).url,/offset=0/);
  h.requests.at(-1).resolve({...neighborPage(),scope:{incomplete:true}});await flush();
  assert.equal(h.get('loadNeighbors').textContent,'本轮读取未完整');assert.match(h.get('neighborReadStatus').textContent,/本次读取不完整/);
});

test('manual refresh preserves zoom, pan, local selection and list position while rereading detail', async () => {
  const h=await loaded();h.choose();
  h.requests[1].resolve({...world,content:'old full body'});await flush();
  h.get('localView').onclick();h.get('zoomIn').onclick();
  h.get('memoryCanvas').events.keydown({key:'ArrowLeft',preventDefault(){}});
  h.frame(6000);const before=JSON.stringify(h.arcs),zoom=h.get('zoomLevel').textContent;
  h.get('nodeList').scrollTop=123;
  h.get('reloadGraph').onclick();
  h.requests[2].resolve(graph([{...world,label:'Updated world'},other]));await flush();
  assert.equal(h.get('zoomLevel').textContent,zoom);
  assert.equal(h.get('localView').classList.contains('chosen'),true);
  assert.equal(h.get('detailTitle').textContent,'Updated world');
  assert.equal(h.get('nodeList').scrollTop,123);
  assert.equal(h.workspace.classList.contains('has-selection'),true);
  assert.match(h.requests[3].url,/record_id=world/);
  h.requests[3].resolve({...world,label:'Updated world',content:'new full body'});await flush();
  assert.equal(h.get('detailBody').textContent,'new full body');
  h.frame(6040);assert.equal(JSON.stringify(h.arcs),before);
  assert.match(h.get('graphRefreshStatus').textContent,/保留当前视图/);
});

test('node omitted by refresh clears selection without claiming the record was deleted', async () => {
  const h=await loaded();h.choose();const oldDetail=h.requests[1];
  h.get('reloadGraph').onclick();h.requests[2].resolve(graph([other]));await flush();
  assert.equal(h.get('detailContent').hidden,true);
  assert.equal(h.get('localView').disabled,true);
  assert.match(h.get('graphRefreshStatus').textContent,/离开当前投影/);
  assert.match(h.get('sourceStatus').title,/不代表原始记录已删除/);
  assert.equal(oldDetail.options.signal.aborted,true);
  oldDetail.resolve({...world,content:'late detail'});await flush();
  assert.equal(h.get('detailContent').hidden,true);
});

test('failed refresh retains data and camera; later retry succeeds', async () => {
  const h=await loaded();h.get('zoomIn').onclick();const zoom=h.get('zoomLevel').textContent;
  h.get('reloadGraph').onclick();h.requests[1].reject(new Error('HTTP 503'));await flush();
  assert.equal(h.get('zoomLevel').textContent,zoom);
  assert.equal(h.get('listCount').textContent,2);
  assert.match(h.get('graphMessage').textContent,/保留上次图谱/);
  assert.equal(h.get('reloadGraph').disabled,false);
  assert.equal(h.get('graphRefreshStatus').textContent,'');
  h.get('reloadGraph').onclick();h.requests[2].resolve(graph());await flush();
  assert.equal(h.get('graphMessage').hidden,true);
  assert.equal(h.get('zoomLevel').textContent,zoom);
});

test('new search resets the view; stale responses and pending debounce cannot overwrite it', async () => {
  const h=await loaded();h.choose();h.get('zoomIn').onclick();
  h.get('reloadGraph').onclick();const oldRefresh=h.requests[2];
  h.get('graphSearch').value='Other';h.get('graphSearch').events.input();
  oldRefresh.resolve(graph());await flush();
  assert.equal(h.get('detailTitle').textContent,'World');
  h.get('reloadGraph').onclick();
  assert.match(h.requests[3].url,/q=Other/);
  h.timer(350);assert.equal(h.requests.length,4);
  h.requests[3].resolve(graph([other],'Other'));await flush();
  assert.equal(h.get('detailContent').hidden,true);
  assert.equal(h.get('listCount').textContent,1);
  assert.equal(h.get('graphRefreshStatus').textContent,'');
  h.requests[1].resolve({...world,content:'late old selection'});await flush();
  assert.equal(h.get('detailContent').hidden,true);
});

test('switching selection aborts the old detail; timeout falls back to the latest summary', async () => {
  const h=await loaded();h.choose(0);h.choose(1);
  assert.equal(h.requests[1].options.signal.aborted,true);
  h.requests[1].resolve({...world,content:'wrong body'});await flush();
  assert.equal(h.get('detailTitle').textContent,'Other');
  h.timer(15000);assert.equal(h.requests[2].options.signal.aborted,true);
  h.requests[2].reject(Object.assign(new Error('aborted'),{name:'AbortError'}));await flush();
  assert.equal(h.get('detailBody').textContent,'summary');
  assert.match(h.get('detailNotice').textContent,/详情读取超时/);
});

test('modal and hidden document stop drawing, including queued frames; closing resumes without duplicate loops', async () => {
  const h=await loaded();h.frame(6000);const draws=h.draws();
  assert.equal(h.raf.size,1);
  h.dialog(true);assert.equal(h.raf.size,0);
  h.get('zoomIn').onclick();h.frame(6040);assert.equal(h.draws(),draws);
  h.visible(false);h.dialog(false);assert.equal(h.raf.size,0);
  h.visible(true);h.visible(true);assert.equal(h.raf.size,1);
  h.frame(6080);assert.equal(h.draws(),draws+1);
  // Also guard a frame that runs before the open-attribute observer callback.
  h.get('relationDiagnostics').open=true;h.frame(6120);
  assert.equal(h.draws(),draws+1);assert.equal(h.raf.size,0);
  h.dialog(false);assert.equal(h.raf.size,1);
});

test('orbit clock advances independently of decorative animation, pauses for reading and resumes without a hidden-tab jump',async()=>{
  const h=await galaxyLoaded(null,1200,systemGraph()),canvas=h.get('memoryCanvas');
  h.get('animateGraph').checked=false;h.get('orbitMotion').checked=true;h.get('orbitSpeed').value='100';h.get('orbitMotion').oninput();
  const before=spatialPositions(h);h.frame(200);h.frame(250);h.frame(300);
  assert.notDeepEqual(spatialPositions(h),before);assert.equal(canvas.dataset.orbitTime,'0.100');assert.equal(h.raf.size,1);
  h.choose();h.frame(400);const paused=spatialPositions(h);assert.equal(h.get('orbitStatus').textContent,'查看暂停');h.frame(500);assert.deepEqual(spatialPositions(h),paused);
  h.get('clearSelection').onclick();h.frame(1000);assert.equal(canvas.dataset.orbitTime,'0.100');h.frame(1050);assert.equal(canvas.dataset.orbitTime,'0.150');
  h.visible(false);h.frame(900000);h.visible(true);h.frame(900100);assert.equal(canvas.dataset.orbitTime,'0.150');h.frame(900150);assert.equal(canvas.dataset.orbitTime,'0.200');
  h.get('orbitSpeed').value='200';h.get('orbitSpeed').oninput();h.frame(900200);h.frame(900250);assert.equal(canvas.dataset.orbitTime,'0.300');
  h.get('orbitMotion').checked=false;h.get('orbitMotion').oninput();h.frame(900300);assert.equal(canvas.dataset.orbitTime,'0.300');assert.equal(h.raf.size,0);
});

test('moving particle bodies can be clicked at their displayed coordinates; hover, gestures and reduced motion stop orbits',async()=>{
  const h=await galaxyLoaded(null,1200,systemGraph()),canvas=h.get('memoryCanvas');
  h.get('orbitMotion').checked=true;h.get('orbitSpeed').value='100';h.get('orbitMotion').oninput();
  h.get('particleBodies').checked=true;h.get('particleBodies').oninput();h.frame(200);h.frame(300);
  assert(Number(canvas.dataset.particleCount)>0);assert(h.workspace.classList.contains('particle-view'));
  const body=h.bodies.find(b=>b.position.role==='planet'),id=body.node.id,{x,y}=body.projection;
  canvas.events.pointermove({pointerId:9,clientX:x,clientY:y});h.frame(400);const time=canvas.dataset.orbitTime;assert.equal(h.get('orbitStatus').textContent,'操作暂停');h.frame(500);assert.equal(canvas.dataset.orbitTime,time);
  pointer(canvas,'pointerdown',9,x,y);h.frame(600);pointer(canvas,'pointerup',9,x,y);h.frame(700);
  const read=new URL(h.requests.at(-1).url,'http://localhost');assert.equal(read.searchParams.get('record_id'),body.node.record_id);assert.equal(read.searchParams.get('tier'),body.node.tier);
  h.get('clearSelection').onclick();canvas.events.pointerleave();h.frame(800);h.frame(900);assert(Number(canvas.dataset.orbitTime)>Number(time));
  pointer(canvas,'pointerdown',1,100,100);pointer(canvas,'pointerdown',2,200,100);h.frame(1000);const pinchTime=canvas.dataset.orbitTime;h.frame(1100);assert.equal(canvas.dataset.orbitTime,pinchTime);
  h.reducedMotion(true);h.frame(1200);assert.equal(h.get('orbitMotion').checked,false);assert.equal(h.get('orbitMotion').disabled,true);assert.equal(canvas.dataset.orbitTime,pinchTime);
  h.reducedMotion(false);assert.equal(h.get('orbitMotion').disabled,false);assert.equal(h.get('orbitMotion').checked,false);
});

test('stored relation endpoints follow the same sampled positions as moving particle hit targets',async()=>{
  const data=systemGraph();data.edges=[{source:world.id,target:'S3:long',kind:'stored_relation',relation:'references'}];
  const h=await galaxyLoaded(null,1200,data);
  h.get('orbitMotion').checked=true;h.get('orbitSpeed').value='100';h.get('orbitMotion').oninput();
  h.get('particleBodies').checked=true;h.get('particleBodies').oninput();h.frame(200);h.frame(300);
  const a=h.bodies.find(b=>b.node.id===world.id).projection,b=h.bodies.find(b=>b.node.id==='S3:long').projection;
  assert(h.lines.some(line=>Math.hypot(line.a.x-a.x,line.a.y-a.y)<1e-7&&Math.hypot(line.b.x-b.x,line.b.y-b.y)<1e-7));
  assert.equal(h.spatial().view.edges.length,1);assert.equal(h.requests.length,1);
});

test('local inspection can resume three-dimensional orbits without losing its selected record, center or real relations',async()=>{
  const {h}=await linked(1200,'3d');h.frame(100);h.get('animateGraph').checked=false;
  h.get('orbitMotion').checked=true;h.get('orbitSpeed').value='100';h.get('orbitMotion').oninput();
  h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.frame(200);
  assert.equal(h.get('orbitPlayback').textContent,'继续运转');const positions=spatialPositions(h),requests=h.requests.length,stats=h.get('graphStats').textContent;
  h.get('orbitPlayback').onclick();h.frame(300);h.frame(400);
  assert.notDeepEqual(spatialPositions(h),positions);assert.equal(h.get('orbitStatus').textContent,'边查看边运转');assert.equal(h.get('orbitPlayback').textContent,'暂停运转');
  assert.equal(h.get('localCenterLabel').textContent,'World');assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.get('graphStats').textContent,stats);assert.equal(h.requests.length,requests);
  const moon=h.spatial().scene.positions.get('S4:far'),parent=h.spatial().scene.positions.get(moon.parent);
  assert(Math.abs(Math.hypot(moon.x-parent.x,moon.y-parent.y,moon.z-parent.z)-moon.orbitRadius)<1e-7);
  h.choose(1);h.frame(500);const time=h.get('memoryCanvas').dataset.orbitTime;h.frame(600);
  assert.equal(h.get('memoryCanvas').dataset.orbitTime,time);assert.equal(h.get('orbitStatus').textContent,'查看暂停');assert.equal(h.get('orbitPlayback').textContent,'继续运转');assert.equal(h.get('detailTitle').textContent,'Other');
  h.get('orbitPlayback').onclick();h.frame(700);h.frame(800);assert(Number(h.get('memoryCanvas').dataset.orbitTime)>Number(time));
  h.get('orbitPlayback').onclick();h.frame(900);assert.equal(h.get('orbitMotion').checked,false);assert.equal(h.get('orbitPlayback').textContent,'开启运转');
});

test('orbit playback guard respects empty scenes, dialogs, 2D and reduced motion; speed readout follows the actual multiplier',async()=>{
  const h=await galaxyLoaded();h.get('orbitSpeed').value='137';h.get('orbitSpeed').oninput();assert.equal(h.get('orbitSpeedValue').textContent,'1.37×');
  h.choose();h.reducedMotion(true);assert.equal(h.get('orbitPlayback').disabled,true);h.get('orbitPlayback').onclick();assert.equal(h.get('orbitMotion').checked,false);
  h.reducedMotion(false);assert.equal(h.get('orbitPlayback').disabled,false);h.dialog(true);h.get('orbitPlayback').onclick();assert.equal(h.get('orbitMotion').checked,false);
  h.dialog(false);h.get('graphDimension').value='2d';h.get('graphDimension').onchange();assert.equal(h.get('orbitPlayback').hidden,true);h.get('orbitPlayback').onclick();assert.equal(h.get('orbitMotion').checked,false);
  const empty=await galaxyLoaded(null,1200,graph([]));assert.equal(empty.get('orbitPlayback').disabled,true);empty.get('orbitPlayback').onclick();assert.equal(empty.get('orbitMotion').checked,false);
});

test('a moving selected satellite remains above the phone drawer and recentring restores reading pause',async()=>{
  const {h}=await linked(390,'3d',phoneLayout());h.frame(100);h.choose();h.get('localView').onclick();h.get('expandLocal').onclick();h.choose(2);
  h.get('orbitSpeed').value='200';h.get('orbitSpeed').oninput();h.get('orbitPlayback').onclick();
  for(let i=0;i<8;i++)h.get('zoomIn').onclick();
  for(let time=200;time<=20200;time+=100){h.frame(time);const [x,y]=bodyPoint(h,'S4:far'),camera=h.spatial().camera;assert(x>=24-1e-6&&x<=camera.width-24+1e-6);assert(y>=24-1e-6&&y<=camera.height-80+1e-6);}
  assert.equal(h.get('detailTitle').textContent,'Far');assert.equal(h.get('localCenterLabel').textContent,'World');h.get('recenterLocal').onclick();h.frame(20300);assert.equal(h.get('orbitPlayback').textContent,'继续运转');assert.equal(h.get('localCenterLabel').textContent,'Far');
});

test('immersive panorama and local fit reserve the header and bottom controls without double-counting the phone drawer',async()=>{
  for(const width of [1280,390]){
    const h=await galaxyLoaded(null,width,systemGraph());h.get('immersiveToggle').onclick();h.frame(200);
    const top=width<=700?172:184,bottom=width<=700?170:110,camera=h.spatial().camera;
    for(const body of h.bodies)assert(body.projection.y>=top&&body.projection.y<=camera.height-bottom);
    h.choose();h.get('localView').onclick();h.frame(300);assert.equal(h.get('detailTitle').textContent,'World');assert.equal(h.get('localCenterLabel').textContent,'World');
    const p=bodyPoint(h);assert(p[1]>=top&&p[1]<=h.spatial().camera.height-32);
  }
});


function inputGraph(){
  const binding={task_id:'task',event_id:'event',action_id:'act',state_version:2,request_hash:'b'.repeat(64)};
  return {schema_version:1,binding,scope:{historical_references_only:true},nodes:[
    {id:'A:act',tier:'A',record_id:'act',label:'Agent 行动',kind:'agent_input_intent',status:'action_unknown',record:{...binding,observation:{status:'unknown'}}},
    {id:'I:old',tier:'I',record_id:'old',label:'S2 输入记忆',kind:'historical_memory_input',record:{tier:'S2',memory_id:'old',integrity_hash:'a'.repeat(64),memory_view:{scope:'memory',revision:7,integrity_hash:'c'.repeat(64)}}}
  ],edges:[{source:'A:act',target:'I:old',kind:'action_input',relation:'used_memory_input'}]};
}
async function inputLoaded(){
  const h=await loaded();goals(h);h.requests.at(-1).resolve(goalGraph());await flush();h.choose();return h;
}
test('explicit input reading links the actual request and historical references without mutation or memory-body lookup',async()=>{
  const h=await inputLoaded(),count=h.requests.length;
  assert.equal(h.get('actionInputs').hidden,false);h.get('loadActionInputs').onclick();
  const read=h.requests.at(-1),url=new URL(read.url,'http://test');
  assert.equal(url.pathname,'/api/action-context/task/graph');assert.equal(url.searchParams.get('event_id'),'event');assert.equal(read.options.method,undefined);
  h.get('loadActionInputs').onclick();assert.equal(h.requests.length,count+1);
  read.resolve(inputGraph());await flush();assert.match(h.get('actionInputStatus').textContent,/已加载 1 个历史输入/);
  assert.match(h.get('graphStats').textContent,/5 节点/);assert.equal(h.get('countA').textContent,'1 / 1');
  h.choose(3);assert.match(h.get('goalStatus').textContent,/结果未知/);assert.equal(h.get('actionInputs').hidden,true);
  h.choose(4);assert.match(h.get('goalSummary').textContent,/输入记忆：S2:old/);assert.match(h.get('goalSummary').textContent,/视图 revision：7/);
  assert.equal(h.requests.length,count+1);h.inspect();assert.equal(h.get('relationKind').textContent,'历史输入引用');
  assert.match(h.get('relationExplanation').textContent,/不提供历史正文/);assert.equal(h.requests.length,count+1);
});
test('late input responses cannot repopulate another selection, source, or refreshed page',async()=>{
  for(const transition of ['selection','source','refresh']){
    const h=await inputLoaded();h.get('loadActionInputs').onclick();const read=h.requests.at(-1);
    if(transition==='selection')h.choose(1);
    else if(transition==='source'){h.get('graphSource').value='memory';h.get('graphSource').onchange();h.requests.at(-1).resolve(graph());await flush();}
    else{h.get('reloadGraph').onclick();h.requests.at(-1).resolve(goalGraph());await flush();}
    assert.equal(read.options.signal.aborted,true);read.resolve(inputGraph());await flush();
    assert.doesNotMatch(h.get('graphStats').textContent,/5 节点/);
  }
});
test('unavailable, mismatched and timed-out input reads preserve the graph and allow explicit retry',async()=>{
  const h=await inputLoaded(),stats=h.get('graphStats').textContent;
  h.get('loadActionInputs').onclick();h.requests.at(-1).reject(new Error('未记录行动输入'));await flush();
  assert.equal(h.get('graphStats').textContent,stats);assert.match(h.get('actionInputStatus').textContent,/保留已有图谱/);
  h.get('loadActionInputs').onclick();const bad=inputGraph();bad.binding.event_id='wrong';h.requests.at(-1).resolve(bad);await flush();
  assert.equal(h.get('graphStats').textContent,stats);assert.match(h.get('actionInputStatus').textContent,/不一致/);
  h.get('loadActionInputs').onclick();const read=h.requests.at(-1);h.timer(15000);const error=new Error();error.name='AbortError';read.reject(error);await flush();
  assert.equal(read.options.signal.aborted,true);assert.equal(h.get('loadActionInputs').disabled,false);assert.match(h.get('actionInputStatus').textContent,/超时/);
});
test('refresh removes supplemental inputs and permits rereading their original request',async()=>{
  const h=await inputLoaded();h.get('loadActionInputs').onclick();h.requests.at(-1).resolve(inputGraph());await flush();
  h.get('reloadGraph').onclick();h.requests.at(-1).resolve(goalGraph());await flush();
  assert.match(h.get('graphRefreshStatus').textContent,/已重置输入引用/);assert.match(h.get('graphStats').textContent,/3 节点/);
  assert.equal(h.get('actionInputs').hidden,false);h.get('loadActionInputs').onclick();assert.match(h.requests.at(-1).url,/action-context/);
});


test('a failed refresh after cancelling input reading leaves an explicit read available',async()=>{
 const h=await inputLoaded();h.get('loadActionInputs').onclick();const old=h.requests.at(-1);
 h.get('reloadGraph').onclick();h.requests.at(-1).reject(new Error('refresh unavailable'));await flush();
 assert.equal(old.options.signal.aborted,true);assert.equal(h.get('loadActionInputs').disabled,false);
 assert.match(h.get('actionInputStatus').textContent,/已取消/);h.get('loadActionInputs').onclick();assert.notEqual(h.requests.at(-1),old);
});
