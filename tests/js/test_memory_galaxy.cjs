const {test} = require('node:test');
const assert = require('node:assert/strict');
const {layout, rotate, project, orbitPoint} = require('../../ui/web/static/memory_galaxy.js');
require('../../ui/web/static/memory_graph.js');
const {buildGraph, visibleGraph} = globalThis.EvaMemoryGraph;
const edge = (source, target, kind = 'stored_relation') => ({source, target, kind, relation:'uses'});
const node = (id, tier = 'S4') => ({id, tier, label:id});
const snapshot = scene => [...scene.positions].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
const finitePoint = point => ['x','y','z'].every(key => Number.isFinite(point[key]));

test('global geometry is stable under reordered nodes and edges; primaries use only visible degree', () => {
  const view = {nodes:[node('c'), node('b'), {...node('a'), degree:100}, node('d','S3'), node('e','S3')], edges:[edge('b','c'), edge('b','d'), edge('d','e')]};
  const first = layout(view), second = layout({nodes:[...view.nodes].reverse(), edges:[...view.edges].reverse()});
  assert.deepEqual(snapshot(first), snapshot(second));
  assert.equal(first.systems.find(system => system.id === 'S4').primary, 'b');
  assert.equal(first.systems.find(system => system.id === 'S3').primary, 'd');
  assert.equal(first.positions.get('a').parent, 'b');
  assert.equal(first.positions.get('a').role, 'planet');
  assert.equal(first.positions.size, view.nodes.length);
});

test('local solar hierarchy follows actual BFS parents through three levels, including incoming traversal', () => {
  const model = buildGraph({nodes:['a','b','c','d','e'].map(id => node(id)), edges:[edge('b','a'),edge('c','b'),edge('d','c'),edge('e','a')]});
  const view = visibleGraph(model, {tiers:new Set(['S4']), origins:true, orphans:true, local:true, center:'a', direction:'incoming', depth:3});
  const scene = layout(view, {local:true, center:'a'});
  assert.deepEqual(scene.positions.get('a'), {x:0,y:0,z:0,role:'star',level:0,parent:null,orbitRadius:0,orbitTilt:{yaw:0,pitch:0},phase:0});
  for (const [id, parent, level, role] of [['b','a',1,'planet'], ['c','b',2,'moon'], ['d','c',3,'moon'], ['e','a',1,'planet']]) {
    const point = scene.positions.get(id), anchor = scene.positions.get(parent);
    assert.equal(point.parent, parent); assert.equal(point.level, level); assert.equal(point.role, role);
    assert.ok(Math.abs(Math.hypot(point.x-anchor.x,point.y-anchor.y,point.z-anchor.z)-point.orbitRadius)<1e-8);
  }
  assert.deepEqual(snapshot(scene), snapshot(layout({...view, nodes:[...view.nodes].reverse()}, {local:true, center:'a'})));
});

test('filtered tiers, relation kinds and depth remain the only nodes in the 3D scene', () => {
  const model = buildGraph({nodes:[node('a'),node('b','S3'),node('c'),node('hidden','S5')], edges:[edge('a','b'),edge('b','c'),edge('a','hidden','provenance')]});
  const before = JSON.stringify(model);
  const opts = {tiers:new Set(['S4','S3']), origins:false, orphans:true, local:true, center:'a', direction:'outgoing', depth:1};
  const view = visibleGraph(model, opts), scene = layout(view, {local:true,center:'a'});
  assert.deepEqual([...scene.positions.keys()].sort(), ['a','b']);
  assert.equal(JSON.stringify(model), before);
  assert.equal(scene.systems.length, 1);
  assert.equal(scene.orbits.length, 1);
});

test('rotation exposes depth and perspective preserves the pan-and-scale projection contract', () => {
  const point = {x:200,y:80,z:100};
  const first = project(point, {yaw:0,pitch:0,distance:1800,width:800,height:600,scale:1,panX:20,panY:30});
  const second = project(point, {yaw:0,pitch:0,distance:1800,width:800,height:600,scale:2,panX:20,panY:30});
  assert.equal(first.perspective, 1800/1700); assert.equal(second.perspective, first.perspective);
  assert.equal(second.x-420, 2*(first.x-420)); assert.equal(second.y-330, 2*(first.y-330));
  assert.equal(project(point,{yaw:.7,pitch:.4}).visible,true);
  assert.notEqual(project(point,{yaw:.7,pitch:.4}).depth,first.depth);
  const rotated=rotate({x:100,y:0,z:0},{yaw:Math.PI/2,pitch:0});
  assert.ok(Math.abs(rotated.x)<1e-8); assert.equal(rotated.z,-100);
});

test('near-camera, behind-camera and invalid numeric inputs never produce unbounded projection', () => {
  for (const z of [1575,1799,1800,2000,1e20]) {
    const point = project({x:1e20,y:-1e20,z},{distance:1800,scale:4,width:900,height:700});
    assert.equal(point.visible,false); assert.ok(Number.isFinite(point.x)); assert.ok(Number.isFinite(point.y));
    assert.ok(Math.abs(point.x)<=1e6); assert.ok(Math.abs(point.y)<=1e6); assert.ok(point.perspective<=8);
  }
  const point=project({x:NaN,y:Infinity,z:undefined},{yaw:Infinity,pitch:NaN,distance:NaN,width:NaN,height:Infinity,scale:Infinity});
  assert.ok(['x','y','depth','perspective'].every(key=>Number.isFinite(point[key])));
  assert.equal(point.visible,true);
  assert.ok(finitePoint(rotate(null,null)));
  assert.ok(finitePoint(orbitPoint(null,Infinity)));
});

test('all 600 nodes fit bounded rings with finite depth without mutating legacy coordinates or adding fake nodes', () => {
  const nodes=Array.from({length:600},(_,i)=>({...node('id-'+String(i).padStart(3,'0'),'S4'),x:i,y:-i,vx:1,vy:2}));
  const edges=nodes.slice(1).map(n=>edge(nodes[0].id,n.id));
  const view={nodes,edges},before=JSON.stringify(view),scene=layout(view);
  assert.equal(scene.positions.size,600); assert.equal(scene.systems.length,1); assert.ok(scene.maxRadius<650);
  assert.equal(JSON.stringify(view),before);
  for(const [id,point] of scene.positions) {assert.ok(nodes.some(n=>n.id===id)); assert.ok(finitePoint(point));}
  const localView={...view,distances:new Map(nodes.map((n,i)=>[n.id,i?1:0])),parents:new Map(nodes.slice(1).map(n=>[n.id,{from:nodes[0].id,to:n.id,edge:edge(nodes[0].id,n.id)}]))};
  const local=layout(localView,{local:true,center:nodes[0].id});
  assert.equal(local.positions.size,600); assert.ok(local.maxRadius<800);
  for(const point of local.positions.values()) assert.ok(finitePoint(point));
});

test('empty, singleton and inconsistent local path metadata do not invent parents', () => {
  assert.equal(layout({nodes:[],edges:[]}).positions.size,0);
  const scene=layout({nodes:[node('a')]},{local:true,center:'a'});
  assert.equal(scene.orbits.length,0); assert.equal(scene.positions.get('a').role,'star');
  const broken=layout({nodes:[node('a'),node('b'),node('c')],distances:new Map([['a',0],['b',2],['c',3]]),parents:new Map([['b',{from:'missing'}],['c',{from:'b'}]])},{local:true,center:'a'});
  assert.equal(broken.positions.size,3); assert.equal(broken.positions.get('b').parent,null); assert.equal(broken.positions.get('c').parent,null);
  for(const point of broken.positions.values()) assert.ok(finitePoint(point));
});

test('sampled orbit points match node positions and tilted rings retain their radius', () => {
  const scene=layout({nodes:['a','b','c','d'].map(id=>node(id)),edges:[edge('a','b'),edge('a','c'),edge('a','d')]});
  for(const point of scene.positions.values()) if(point.role!=='star') {
    const parent=scene.positions.get(point.parent), orbit={center:parent,radius:point.orbitRadius,tilt:point.orbitTilt};
    const sample=orbitPoint(orbit,point.phase);
    for(const key of ['x','y','z']) assert.ok(Math.abs(sample[key]-point[key])<1e-8);
    const opposite=orbitPoint(orbit,point.phase+Math.PI);
    assert.ok(Math.abs(Math.hypot(sample.x-opposite.x,sample.y-opposite.y,sample.z-opposite.z)-point.orbitRadius*2)<1e-8);
  }
});

test('animated tier systems retain distinct radii and speeds, carry their planets and never mutate the base scene',()=>{
  const {sample}=require('../../ui/web/static/memory_galaxy.js');
  const nodes=['S4','S3','S2','S1','S5'].flatMap(tier=>[node(tier+'a',tier),node(tier+'b',tier)]);
  const scene=layout({nodes,edges:[]}),before=JSON.stringify(snapshot(scene)),pose=sample(scene,37);
  assert.equal(pose.positions.size,10);assert.deepEqual(snapshot(pose),snapshot(sample(scene,37)));
  assert.equal(JSON.stringify(snapshot(scene)),before);
  assert.deepEqual(pose.positions.get('S4a'),scene.positions.get('S4a'));
  assert.notDeepEqual(pose.positions.get('S3a'),scene.positions.get('S3a'));
  assert.equal(new Set([...scene.motions.values()].filter(m=>m.orbit.level===0).map(m=>m.orbit.radius)).size,4);
  assert(scene.motions.get('S1b').speed>scene.motions.get('S2b').speed);
  assert(scene.motions.get('S2b').speed>scene.motions.get('S3b').speed);
  for(const [id,p] of pose.positions){
    const motion=scene.motions.get(id);if(!motion)continue;
    const c=p.parent?pose.positions.get(p.parent):motion.orbit.center;
    assert(Math.abs(Math.hypot(p.x-c.x,p.y-c.y,p.z-c.z)-p.orbitRadius)<1e-7);
  }
  for(const ring of pose.orbits)if(ring.parent)assert.deepEqual(ring.center,pose.positions.get(ring.parent));
  for(const system of pose.systems)assert.deepEqual(system.center,pose.positions.get(system.primary));
});

test('three nested local orbital levels follow their moving parents at large and invalid clock values',()=>{
  const {sample}=require('../../ui/web/static/memory_galaxy.js');
  const model=buildGraph({nodes:['a','b','c','d'].map(id=>node(id)),edges:[edge('a','b'),edge('b','c'),edge('c','d')]});
  const view=visibleGraph(model,{tiers:new Set(['S4']),origins:true,orphans:true,local:true,center:'a',depth:3});
  const scene=layout(view,{local:true,center:'a'});
  for(const time of [0,4,1e20,Infinity,NaN,-10]){
    const pose=sample(scene,time);assert.equal(pose.positions.size,4);
    for(const [id,p] of pose.positions){assert(finitePoint(p));if(p.parent){const c=pose.positions.get(p.parent);assert(Math.abs(Math.hypot(p.x-c.x,p.y-c.y,p.z-c.z)-p.orbitRadius)<1e-7);}}
  }
});


test('historical input references have bounded global systems and follow the actual local request-action path',()=>{
 const nodes=[node('G:request','G'),node('A:action','A'),node('I:input','I')],edges=[
  {source:'G:request',target:'A:action',kind:'action_input',relation:'request_agent_intent'},
  {source:'A:action',target:'I:input',kind:'action_input',relation:'used_memory_input'}];
 const graph=buildGraph({nodes,edges}),view=visibleGraph(graph,{tiers:new Set(['G','A','I']),origins:true,orphans:true});
 const global=layout(view);assert.equal(global.positions.size,3);assert.equal(global.systems.find(s=>s.id==='I').label,'历史输入引用');
 for(const point of global.positions.values())assert(finitePoint(point));
 const local=visibleGraph(graph,{tiers:new Set(['G','A','I']),origins:true,orphans:true,local:true,center:'G:request',depth:2,direction:'outgoing'});
 const scene=layout(local,{local:true,center:'G:request'});assert.equal(scene.positions.get('I:input').parent,'A:action');assert.equal(scene.positions.size,3);
});
