const {test} = require('node:test');
const assert = require('node:assert/strict');
const math = require('../../ui/web/static/memory_galaxy.js');
const renderer = require('../../ui/web/static/memory_galaxy_renderer.js');

function recordingContext() {
  const records = [], stack = [];
  let path = [], dash = [];
  const ctx = {
    globalAlpha:1, lineWidth:1, strokeStyle:'', fillStyle:'', font:'', textAlign:'', textBaseline:'',
    clearRect(...rect) {records.push({kind:'clear', rect});},
    save() {stack.push({globalAlpha:this.globalAlpha, lineWidth:this.lineWidth, strokeStyle:this.strokeStyle, fillStyle:this.fillStyle, dash:[...dash]});},
    restore() {const old=stack.pop(); dash=old.dash; Object.assign(this, old);},
    beginPath() {path=[];}, rect(...args) {path.push(['rect', ...args]);}, clip() {},
    moveTo(...args) {path.push(['move', ...args]);}, lineTo(...args) {path.push(['line', ...args]);}, arc(...args) {path.push(['arc', ...args]);},
    setLineDash(value) {dash=[...value];},
    stroke() {records.push({kind:'stroke', path:[...path], style:this.strokeStyle, width:this.lineWidth, alpha:this.globalAlpha, dash:[...dash]});},
    fill() {records.push({kind:'fill', path:[...path], style:this.fillStyle, alpha:this.globalAlpha});},
    createRadialGradient(...args) {return {kind:'gradient', args, stops:[], addColorStop(at, color) {this.stops.push([at,color]);}};},
    measureText(value) {return {width:String(value).length*6};},
    fillText(value, x, y) {records.push({kind:'text',value,x,y,style:this.fillStyle,alpha:this.globalAlpha});}
  };
  return {ctx,records};
}
const camera = {yaw:0,pitch:0,scale:1,panX:0,panY:0,width:900,height:700,distance:1800};
const paint = {edge:'#73717b',origin:'#927386',path:'#36343d',highlight:'#252528',text:'#252528',muted:'#64616c'};
const nodes = ['a','b','c'].map((id,i)=>({id,tier:'S4',label:'节点 '+id,color:'#7c629d',degree:i}));
const positions = new Map([
  ['a',{x:-200,y:0,z:-300,role:'planet',level:1}],
  ['b',{x:0,y:40,z:0,role:'star',level:0}],
  ['c',{x:200,y:0,z:400,role:'moon',level:2}]
]);
const base = {scene:{positions,orbits:[],systems:[]},view:{nodes,edges:[]},camera,paint,showLabels:false,showOrbits:false,animate:false,edgeBrightness:.5};
const edgeIdentity = edge=>JSON.stringify([edge.source,edge.target,edge.relation,edge.kind]);
const straightStrokes = records=>records.filter(r=>r.kind==='stroke'&&r.path.length===2&&r.path[0][0]==='move'&&r.path[1][0]==='line');

test('spherical bodies paint far to near using true perspective, preserve positions and clear the physical canvas',()=>{
  const h=recordingContext(), before=JSON.stringify([...positions]);
  const result=renderer.draw(h.ctx,{...base,view:{nodes:[nodes[2],nodes[0],nodes[1]],edges:[]},clearWidth:1100,clearHeight:800});
  assert.deepEqual(h.records[0],{kind:'clear',rect:[0,0,1100,800]});
  assert.deepEqual(result.bodies.map(body=>body.node.id),['a','b','c']);
  const spheres=h.records.filter(r=>r.kind==='fill'&&r.style?.stops?.length===3);
  assert.equal(spheres.length,3);
  spheres.forEach((sphere,i)=>{
    const body=result.bodies[i], expected=math.project(positions.get(body.node.id),camera);
    assert.deepEqual(sphere.path[0].slice(1,4),[expected.x,expected.y,body.radius]);
    assert.equal(body.projection.depth,expected.depth);
  });
  assert.equal(JSON.stringify([...positions]),before);
  assert.equal(h.ctx.globalAlpha,1);
});

test('hierarchical radius distinguishes stellar primaries, planets and satellites and follows perspective',()=>{
  const projection={perspective:1}, node={degree:12};
  const star=renderer.bodyRadius(node,{role:'star'},projection,1), planet=renderer.bodyRadius(node,{role:'planet'},projection,1), moon=renderer.bodyRadius(node,{role:'moon'},projection,1);
  assert(star>planet*1.6); assert(planet>moon*1.5);
  assert(renderer.bodyRadius(node,{role:'planet'},{perspective:1.5},1)>planet);
  assert(renderer.bodyRadius(node,{role:'planet'},projection,2)>planet);
  assert(renderer.bodyRadius(node,{role:'star'},{perspective:8},8)<=46);
  assert(Number.isFinite(renderer.bodyRadius({},null,null,NaN)));
});

test('only supplied supported relations draw, provenance stays dashed, self loops and path identity are retained',()=>{
  const edges=[
    {source:'a',target:'b',kind:'stored_relation',relation:'owns'},
    {source:'b',target:'c',kind:'provenance',relation:'source_event'},
    {source:'a',target:'a',kind:'stored_relation',relation:'self'},
    {source:'a',target:'c',kind:'invented_similarity',relation:'same'},
    {source:'a',target:'missing',kind:'stored_relation',relation:'missing'}
  ];
  const h=recordingContext();
  renderer.draw(h.ctx,{...base,view:{nodes,edges},pathEdges:new Set([edgeIdentity(edges[0])])});
  const lines=straightStrokes(h.records);
  assert.equal(lines.length,2);
  assert.equal(lines[0].style,paint.path); assert.equal(lines[0].width,2.1);
  assert.equal(lines[1].style,paint.origin); assert.deepEqual(lines[1].dash,[3,5]);
  const a=math.project(positions.get('a'),camera),b=math.project(positions.get('b'),camera);
  assert.deepEqual(lines[0].path,[['move',a.x,a.y],['line',b.x,b.y]]);
  assert.equal(h.records.filter(r=>r.kind==='stroke'&&r.path[0]?.[0]==='arc'&&r.path[0][3]===13).length,1);
});

test('local arrows retain source to target direction and stop outside the target body',()=>{
  const edge={source:'a',target:'c',kind:'stored_relation',relation:'references'},h=recordingContext();
  const result=renderer.draw(h.ctx,{...base,view:{nodes,edges:[edge]},localCenter:'a'});
  const arrow=h.records.find(r=>r.kind==='stroke'&&r.path.length===3&&r.path[0][0]==='move'&&r.path[1][0]==='line'&&r.path[2][0]==='line');
  assert(arrow);
  const target=result.bodies.find(body=>body.node.id==='c'), tip=arrow.path[1];
  assert(Math.abs(Math.hypot(tip[1]-target.projection.x,tip[2]-target.projection.y)-(target.radius+5))<1e-8);
  assert(tip[1]<target.projection.x);
});

test('orbit guides are optional, bounded and separate from data relations',()=>{
  const orbits=Array.from({length:80},(_,i)=>({center:{x:0,y:0,z:0},radius:80+i,tilt:{yaw:.3,pitch:.5},level:1}));
  const plain=recordingContext(),guides=recordingContext();
  renderer.draw(plain.ctx,{...base,scene:{...base.scene,orbits}});
  renderer.draw(guides.ctx,{...base,scene:{...base.scene,orbits},showOrbits:true});
  const guideStrokes=guides.records.filter(r=>r.kind==='stroke'&&r.path[0]?.[0]==='move'&&r.width===.7);
  assert.equal(guideStrokes.length,60);
  assert(guideStrokes.every(r=>r.path.length<=144));
  assert.equal(straightStrokes(plain.records).length,0);
  assert.equal(straightStrokes(guides.records).length,0);
});

test('disabled animation is time independent while enabled animation changes decoration without moving bodies',()=>{
  const edge={source:'a',target:'b',kind:'stored_relation',relation:'owns'},opts={...base,view:{nodes,edges:[edge]}};
  const first=recordingContext(),second=recordingContext(),animated=recordingContext();
  const a=renderer.draw(first.ctx,{...opts,time:1000}),b=renderer.draw(second.ctx,{...opts,time:7500}),c=renderer.draw(animated.ctx,{...opts,time:7500,animate:true});
  const clean=records=>JSON.stringify(records,(_,value)=>typeof value==='function'?undefined:value);
  assert.equal(clean(first.records),clean(second.records));
  assert.notEqual(clean(second.records),clean(animated.records));
  assert.deepEqual(a.bodies.map(body=>body.projection),b.bodies.map(body=>body.projection));
  assert.deepEqual(b.bodies.map(body=>body.projection),c.bodies.map(body=>body.projection));
});

test('near plane rejects hidden bodies and their relations without producing invalid canvas coordinates',()=>{
  const hidden=new Map(positions); hidden.set('c',{x:1,y:1,z:1800,role:'planet'});
  const h=recordingContext(),result=renderer.draw(h.ctx,{...base,scene:{positions:hidden},view:{nodes,edges:[{source:'a',target:'c',kind:'stored_relation',relation:'hidden'}]}});
  assert.deepEqual(result.bodies.map(body=>body.node.id),['a','b']);
  assert.equal(straightStrokes(h.records).length,0);
  for(const record of h.records)for(const command of record.path||[])assert(command.slice(1).every(Number.isFinite));
});

test('labels use canvas measurements, prioritize selected records, and stay away from bottom controls',()=>{
  const many=Array.from({length:60},(_,i)=>({id:'n'+i,label:'记录 '+i,color:'#7c629d',degree:i}));
  const pts=new Map(many.map((node,i)=>[node.id,{x:-350+(i%10)*75,y:-260+Math.floor(i/10)*90,z:0,role:'planet'}]));
  const h=recordingContext();renderer.draw(h.ctx,{...base,scene:{positions:pts},view:{nodes:many,edges:[]},showLabels:true,localCenter:'n0',selected:'n59'});
  const labels=h.records.filter(record=>record.kind==='text');
  assert(labels.length>0&&labels.length<=35);
  assert.equal(labels[0].value,'记录 59');
  assert(labels.every(record=>record.y<=camera.height-89));
  assert(labels.every(record=>record.style===paint.text||record.style===paint.muted));
});

test('crowded labels avoid visible spheres and selected record names precede relation annotations',()=>{
  const crowded=new Map(positions);crowded.set('c',{x:0,y:69,z:0,role:'moon',level:2});
  const edges=[{source:'a',target:'b',kind:'stored_relation',relation:'owns'}],h=recordingContext();
  const result=renderer.draw(h.ctx,{...base,scene:{positions:crowded},view:{nodes,edges},showLabels:true,localCenter:'b',selected:'b'});
  const labels=h.records.filter(record=>record.kind==='text');assert.equal(labels[0].value,'节点 b');
  const primary=result.bodies.find(body=>body.node.id==='b');
  assert.notEqual(labels[0].y,primary.projection.y+primary.radius+19);
  for(const text of labels){
    const size=text.value==='节点 b'?11:10,half=text.value.length*3;
    const box={left:text.x-half-4,right:text.x+half+4,top:text.y-size-3,bottom:text.y+3};
    for(const body of result.bodies){const p=body.projection,r=body.radius+3;
      assert(!(box.left<p.x+r&&box.right>p.x-r&&box.top<p.y+r&&box.bottom>p.y-r),text.value+' overlaps '+body.node.id);
    }
  }
});

test('indirect local bodies stay readable while global selection still dims unrelated systems',()=>{
  const opts={...base,view:{nodes,edges:[{source:'a',target:'b',kind:'stored_relation',relation:'owns'}]},selected:'b'};
  const local=recordingContext(),global=recordingContext();
  renderer.draw(local.ctx,{...opts,localCenter:'b'});renderer.draw(global.ctx,opts);
  const spheres=h=>h.records.filter(record=>record.kind==='fill'&&record.style?.stops?.length===3);
  assert(spheres(local)[2].alpha>.5);assert(spheres(global)[2].alpha<.5);
  assert.equal(spheres(local)[1].alpha,1);assert.equal(spheres(global)[1].alpha,1);
});

test('an observed system labels its low-degree planets at phone zoom and stays bright with an outside selection',()=>{
  const pts=new Map(positions);pts.set('a',{...pts.get('a'),parent:'b'});
  const h=recordingContext(),before=JSON.stringify([...pts]);
  const result=renderer.draw(h.ctx,{...base,scene:{positions:pts,systems:[{id:'S4',primary:'b',label:'世界模型'}]},camera:{...camera,scale:.7},selected:'c',observedSystem:'S4',showLabels:true});
  assert.equal(h.records.filter(record=>record.kind==='fill'&&record.style?.stops?.length===3)[0].alpha,1);
  const texts=h.records.filter(record=>record.kind==='text').map(record=>record.value);
  assert.ok(texts.includes('节点 a'));assert.ok(!texts.includes('节点 c'));
  assert.equal(result.bodies.length,3);assert.equal(JSON.stringify([...pts]),before);
  assert.equal(straightStrokes(h.records).length,0);
});

test('particle bodies project XYZ clouds with bounded cost, preserve hit bodies and remain deterministic when decoration is off',()=>{
  const h=recordingContext(),result=renderer.draw(h.ctx,{...base,particleBodies:true,time:10});
  const other=recordingContext();renderer.draw(other.ctx,{...base,particleBodies:true,time:9000});
  assert.equal(JSON.stringify(h.records),JSON.stringify(other.records));assert.equal(result.bodies.length,3);
  assert(result.particles>50&&result.particles<=2240);
  assert.equal(h.records.filter(r=>r.kind==='fill'&&r.style?.stops?.length===3).length,0);
  const rotated=recordingContext();renderer.draw(rotated.ctx,{...base,particleBodies:true,camera:{...camera,yaw:.5,pitch:.7}});
  assert.notDeepEqual(rotated.records,h.records);
  const many=Array.from({length:600},(_,i)=>({...nodes[0],id:String(i)})),positions=new Map(many.map((n,i)=>[n.id,{x:i%30*10-150,y:Math.floor(i/30)*10-100,z:i%20,role:'planet'}]));
  const full=renderer.draw(recordingContext().ctx,{...base,particleBodies:true,scene:{positions,orbits:[]},view:{nodes:many,edges:[]}});
  assert.equal(full.bodies.length,600);assert(full.particles<=2240);assert(full.particles>=600);
});

test('particle budget prioritizes primary density and remains stable when depth order changes',()=>{
  const nodes=Array.from({length:100},(_,i)=>({id:String(i),label:String(i),color:'#7c629d'}));
  const positions=new Map(nodes.map((node,i)=>[node.id,{x:i%10*35-150,y:Math.floor(i/10)*35-150,z:i%7*12,role:i===0?'star':i===1?'moon':'planet'}]));
  const opts={...base,particleBodies:true,scene:{positions,orbits:[]},view:{nodes,edges:[]}};
  const result=renderer.draw(recordingContext().ctx,opts),counts=new Map(result.bodies.map(body=>[body.node.id,body.particleCount]));
  assert.equal([...counts.values()].reduce((sum,n)=>sum+n,0),1600);
  assert(counts.get('0')>counts.get('2'));assert(counts.get('2')>counts.get('1'));assert([...counts.values()].every(n=>n>0));
  const reversed=renderer.draw(recordingContext().ctx,{...opts,camera:{...camera,yaw:Math.PI}});
  for(const body of reversed.bodies)assert.equal(body.particleCount,counts.get(body.node.id));
});

test('particle system names precede unselected record labels and avoid toolbar exclusion rectangles',()=>{
  const boxes=[{left:390,right:510,top:360,bottom:405}],h=recordingContext();
  const opts={...base,particleBodies:true,showLabels:true,labelExclusions:boxes,scene:{positions,systems:[{id:'S4',primary:'b',label:'世界模型'}]}};
  renderer.draw(h.ctx,opts);const texts=h.records.filter(record=>record.kind==='text');
  assert.equal(texts[0].value,'S4 · 世界模型');
  for(const text of texts){const size=text.value==='节点 b'?10:10,half=text.value.length*3,box={left:text.x-half-4,right:text.x+half+4,top:text.y-size-3,bottom:text.y+3};
    assert(!boxes.some(b=>box.left<b.right&&box.right>b.left&&box.top<b.bottom&&box.bottom>b.top));}
  const selected=recordingContext();renderer.draw(selected.ctx,{...opts,selected:'a'});
  assert.equal(selected.records.find(record=>record.kind==='text').value,'节点 a');
});
