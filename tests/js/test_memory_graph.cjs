const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../../ui/web/static/memory_graph.js');
const {buildGraph,visibleGraph,mergeNeighborhood,step}=globalThis.EvaMemoryGraph;
const {hitEdges,connectionPath,edgeIdentity}=globalThis.EvaMemoryGraph;
const {relationKey,relationChoices}=globalThis.EvaMemoryGraph;

test('exact relation filtering constrains every path step without matching substrings or merging edge kinds',()=>{
  const g=buildGraph({nodes:['a','b','c','d','e'].map(id=>({id,tier:'S4'})),edges:[
    {source:'a',target:'b',kind:'stored_relation',relation:'owns'},
    {source:'b',target:'c',kind:'stored_relation',relation:'uses'},
    {source:'b',target:'d',kind:'stored_relation',relation:'owns'},
    {source:'a',target:'e',kind:'stored_relation',relation:'owns_part'},
    {source:'a',target:'c',kind:'provenance',relation:'owns'}]});
  const opts={tiers:new Set(['S4']),origins:true,orphans:true,local:true,center:'a',depth:3,relationName:relationKey(g.edges[0])};
  const v=visibleGraph(g,opts);assert.deepEqual(v.nodes.map(n=>n.id),['a','b','d']);
  assert.deepEqual(connectionPath(v,'a','d').map(s=>s.edge.relation),['owns','owns']);
  assert.equal(connectionPath(v,'a','c'),null);assert.equal(v.edges.length,2);
  assert.equal(visibleGraph(g,{...opts,local:false}).edges.length,5);
});

test('relation choices preserve exact hostile names, separate kinds, and retain unavailable selection at zero',()=>{
  const stored={kind:'stored_relation',relation:'source_event'},origin={kind:'provenance',relation:'source_event'},literal={kind:'stored_relation',relation:'<img onerror=x>"&'};
  const missing=relationKey({kind:'stored_relation',relation:'gone'}),choices=relationChoices([stored,origin,literal,literal],missing);
  assert.equal(choices.length,4);assert.equal(choices.find(c=>c.key===missing).count,0);
  assert.equal(choices.find(c=>c.key===relationKey(literal)).count,2);
  assert.match(choices.find(c=>c.key===relationKey(literal)).label,/<img onerror=x>"&/);
  assert.notEqual(choices.find(c=>c.key===relationKey(stored)).label,choices.find(c=>c.key===relationKey(origin)).label);
  assert.deepEqual(relationChoices([literal,origin,stored,literal],missing),choices);
});

test('local paths choose a stable shortest route across cycles, reordered and parallel edges',()=>{
  const nodes=['a','b','c','d'].map(id=>({id,tier:'S4'})),edges=[
    ['a','c','via_c'],['c','d','finish'],['a','b','z'],['a','b','a'],['b','d','finish'],['d','a','cycle'],['a','a','self']
  ].map(([source,target,relation])=>({source,target,relation,kind:'stored_relation'}));
  const opts={tiers:new Set(['S4']),origins:true,orphans:true,local:true,center:'a',direction:'outgoing',depth:3};
  const first=connectionPath(visibleGraph(buildGraph({nodes,edges}),opts),'a','d');
  assert.deepEqual(first.map(s=>[s.from,s.to,s.edge.relation]),[['a','b','a'],['b','d','finish']]);
  const second=connectionPath(visibleGraph(buildGraph({nodes:[...nodes].reverse(),edges:[...edges].reverse()}),opts),'a','d');
  assert.deepEqual(first.map(s=>edgeIdentity(s.edge)),second.map(s=>edgeIdentity(s.edge)));
});

test('incoming paths retain the stored direction and shortest length even when traversal is reversed',()=>{
  const g=buildGraph({nodes:['a','b','c'].map(id=>({id,tier:'S4'})),edges:[['b','a'],['c','b']].map(([source,target])=>({source,target,relation:'r',kind:'stored_relation'}))});
  const opts={tiers:new Set(['S4']),origins:true,orphans:true,local:true,center:'a',direction:'incoming',depth:3};
  const p=connectionPath(visibleGraph(g,opts),'a','c');
  assert.deepEqual(p.map(s=>[s.from,s.to,s.edge.source,s.edge.target]),[['a','b','b','a'],['b','c','c','b']]);
  assert.equal(connectionPath(visibleGraph(g,{...opts,direction:'outgoing'}),'a','c'),null);
});

test('paths never bypass hidden tiers, provenance, categories or the local depth',()=>{
  const g=buildGraph({nodes:[{id:'a',tier:'S4'},{id:'b',tier:'S5'},{id:'c',tier:'S4'}],edges:[
    {source:'a',target:'b',kind:'provenance',relation:'source_event'},
    {source:'b',target:'c',kind:'stored_relation',relation:'r'}]});
  const opts={tiers:new Set(['S4','S5']),origins:true,orphans:true,local:true,center:'a',depth:2};
  assert.equal(connectionPath(visibleGraph(g,opts),'a','c').length,2);
  for(const change of [{tiers:new Set(['S4'])},{origins:false},{relationKind:'stored_relation'},{depth:1},{local:false}])
    assert.equal(connectionPath(visibleGraph(g,{...opts,...change}),'a','c'),null);
  assert.deepEqual(connectionPath(visibleGraph(g,opts),'a','a'),[]);
});
test('edge hit testing retains parallel and crossing candidates without extending line segments',()=>{
  const a={x:0,y:0},b={x:100,y:0},c={x:50,y:-50},d={x:50,y:50};
  const edges=[{source:'a',target:'b',relation:'owns',kind:'stored_relation',a,b},
    {source:'a',target:'b',relation:'uses',kind:'stored_relation',a,b},
    {source:'c',target:'d',relation:'crosses',kind:'stored_relation',a:c,b:d}];
  assert.equal(hitEdges(edges,n=>n,50,1).length,3);
  assert.equal(hitEdges(edges,n=>n,120,0).length,0);
  assert.equal(hitEdges(edges,n=>({x:n.x*2+30,y:n.y*2+20}),130,22).length,3);
  assert.equal(hitEdges([edges[0],edges[0]],n=>n,40,0).length,1);
});
test('self-loop hit geometry matches its displayed circle and ignores collapsed non-self edges',()=>{
  const a={x:10,y:20};const loop={source:'a',target:'a',kind:'stored_relation',relation:'self',a,b:a};
  assert.equal(hitEdges([loop],n=>n,33,10).length,1);
  assert.equal(hitEdges([loop],n=>n,20,10).length,0);
  assert.equal(hitEdges([{...loop,target:'b'}],n=>n,10,20).length,0);
});
const data={nodes:[{id:'world',tier:'S4'},{id:'memory',tier:'S3'},{id:'event',tier:'S5'},{id:'orphan',tier:'S2'}],edges:[
  {source:'world',target:'memory',kind:'stored_relation',relation:'knows'},
  {source:'memory',target:'event',kind:'provenance',relation:'source_event'},
  {source:'world',target:'missing',kind:'stored_relation'},
  {source:'world',target:'event',kind:'invented_similarity'}]};
test('only supplied, resolvable relations survive; inputs are not mutated',()=>{
  const before=JSON.stringify(data),g=buildGraph(data);
  assert.equal(g.edges.length,2);assert.equal(g.nodes.length,4);assert.equal(JSON.stringify(data),before);
  for(const e of g.edges){assert(g.map.has(e.source));assert(g.map.has(e.target));}
});
test('local graph and origin/orphan filters respect exact neighbors',()=>{
  const g=buildGraph(data),options={tiers:new Set(['S2','S3','S4','S5']),origins:true,orphans:true,local:true,selected:'world'};
  assert.deepEqual(visibleGraph(g,options).nodes.map(n=>n.id),['world','memory']);
  const filtered=visibleGraph(g,{...options,local:false,origins:false,orphans:false});
  assert.deepEqual(filtered.nodes.map(n=>n.id),['world','memory']);assert.equal(filtered.edges.length,1);
  assert.equal(visibleGraph(g,{...options,tiers:new Set()}).nodes.length,0);
});
test('local expansion follows bounded shortest paths with direction, cycles and parallel relations',()=>{
  const g=buildGraph({nodes:['a','b','c','d','e','in'].map(id=>({id,tier:'S4'})),edges:[
    ['a','b'],['a','b'],['b','c'],['c','a'],['c','d'],['d','e'],['in','a'],['a','a']
  ].map(([source,target],i)=>({source,target,kind:'stored_relation',relation:'r'+i}))});
  const opts={tiers:new Set(['S4']),origins:true,orphans:false,local:true,center:'a',selected:'b',direction:'outgoing',depth:1};
  const one=visibleGraph(g,opts);assert.deepEqual(one.nodes.map(n=>n.id),['a','b']);assert.equal(one.edges.length,3);
  const two=visibleGraph(g,{...opts,depth:2});assert.deepEqual(two.nodes.map(n=>n.id),['a','b','c']);assert.equal(two.distances.get('c'),2);
  assert.deepEqual(visibleGraph(g,{...opts,depth:99}).nodes.map(n=>n.id),['a','b','c','d']);
  assert.deepEqual(visibleGraph(g,{...opts,direction:'incoming'}).nodes.map(n=>n.id),['a','c','in']);
  assert.deepEqual(visibleGraph(g,{...opts,direction:'both'}).nodes.map(n=>n.id),['a','b','c','in']);
});
test('local filtering never traverses hidden tiers or origins and retains an isolated anchor',()=>{
  const g=buildGraph(data),opts={tiers:new Set(['S2','S3','S4','S5']),origins:true,orphans:false,local:true,center:'world',depth:3};
  assert.deepEqual(visibleGraph(g,opts).nodes.map(n=>n.id),['world','memory','event']);
  assert.deepEqual(visibleGraph(g,{...opts,origins:false}).nodes.map(n=>n.id),['world','memory']);
  assert.deepEqual(visibleGraph(g,{...opts,relationKind:'provenance'}).nodes.map(n=>n.id),['world']);
  assert.deepEqual(visibleGraph(g,{...opts,tiers:new Set(['S4','S5'])}).nodes.map(n=>n.id),['world']);
  assert.deepEqual(visibleGraph(g,{...opts,center:'orphan'}).nodes.map(n=>n.id),['orphan']);
  assert.equal(visibleGraph(g,{...opts,tiers:new Set(['S3'])}).nodes.length,0);
});
test('neighbor pages merge exact edges without duplicating or losing parallel relations',()=>{
  const g=buildGraph(data),incoming={nodes:[{id:'world',tier:'S4'},{id:'new',tier:'S4'}],edges:[
    {source:'world',target:'new',kind:'stored_relation',relation:'owns'},
    {source:'world',target:'new',kind:'stored_relation',relation:'uses'},
    {source:'world',target:'absent',kind:'stored_relation',relation:'missing'}]};
  const one=mergeNeighborhood(g,incoming);assert.equal(one.addedNodes,1);assert.equal(one.addedEdges,2);assert.equal(one.omitted,1);
  const two=mergeNeighborhood(one,incoming);assert.equal(two.addedNodes,0);assert.equal(two.addedEdges,0);
  assert.equal(two.edges.length,g.edges.length+2);assert.equal(g.nodes.length,4);
});
test('neighbor merge enforces capacity atomically without leaving dangling endpoints',()=>{
  const g=buildGraph(data),incoming={nodes:[{id:'new',tier:'S4'}],edges:[{source:'world',target:'new',kind:'stored_relation',relation:'owns'}]};
  for(const [nodes,edges] of [[4,3000],[600,2]]){
    const merged=mergeNeighborhood(g,incoming,nodes,edges);assert.equal(merged.omitted,1);
    assert.equal(merged.nodes.length,4);assert.equal(merged.edges.length,2);
  }
});
test('layout is deterministic and finite, including collisions and fixed nodes',()=>{
  const a=buildGraph(data),b=buildGraph(data);assert.deepEqual(a.nodes,b.nodes);
  for(const n of a.nodes){n.x=0;n.y=0;}
  for(let i=0;i<200;i++)step(a,'world');
  assert.equal(a.map.get('world').x,0);assert.equal(a.map.get('world').y,0);
  for(const n of a.nodes){assert(Number.isFinite(n.x));assert(Number.isFinite(n.y));}
  assert.equal(a.edges.length,2);
});
test('node and edge caps are enforced',()=>{
  const nodes=Array.from({length:700},(_,i)=>({id:String(i),tier:'S4'}));
  const edges=Array.from({length:3500},()=>({source:'1',target:'2',kind:'stored_relation'}));
  const g=buildGraph({nodes,edges});assert.equal(g.nodes.length,600);assert.equal(g.edges.length,3000);assert.equal(g.springs.length,1);
});


test('action input merging is atomic, idempotent and rejects mismatched identity or capacity',()=>{
 const {mergeActionInputs}=globalThis.EvaMemoryGraph;
 const anchor={id:'G:g',tier:'G',record:{task_id:'t',source_event_id:'e'}},binding={task_id:'t',event_id:'e',action_id:'a',state_version:2,request_hash:'h'};
 const current=buildGraph({nodes:[anchor],edges:[]}),incoming={schema_version:1,binding,scope:{historical_references_only:true},
   nodes:[{id:'A:a',tier:'A',record:{...binding}},{id:'I:i',tier:'I'}],edges:[{source:'A:a',target:'I:i',kind:'action_input',relation:'used_memory_input'}]};
 const merged=mergeActionInputs(current,incoming,anchor);assert.equal(merged.nodes.length,3);assert.equal(merged.edges.length,2);
 assert.equal(mergeActionInputs(merged,incoming,anchor).edges.length,2);assert.equal(current.nodes.length,1);
 assert.throws(()=>mergeActionInputs(current,{...incoming,binding:{...binding,event_id:'wrong'}},anchor));
 assert.throws(()=>mergeActionInputs(current,incoming,anchor,2));assert.equal(current.nodes.length,1);
 assert.throws(()=>mergeActionInputs(current,{...incoming,edges:[{...incoming.edges[0],target:'missing'}]},anchor));
 const local=visibleGraph(buildGraph(merged),{tiers:new Set(['G','A','I']),origins:true,orphans:true,local:true,center:'G:g',depth:2,relationKind:'action_input'});
 assert.equal(connectionPath(local,'G:g','I:i').length,2);
});
