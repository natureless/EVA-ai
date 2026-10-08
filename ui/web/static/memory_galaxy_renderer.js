/* Display-only stellar geometry. Orbit guides never create memory relations. */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.EvaMemoryGalaxyRenderer = api;
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  'use strict';
  const TAU = Math.PI * 2, FONT = '"Segoe UI","Microsoft YaHei",sans-serif';
  const clamp = (n, low, high) => Math.max(low, Math.min(high, Number.isFinite(Number(n)) ? Number(n) : low));
  const identity = edge => JSON.stringify([edge.source, edge.target, edge.relation, edge.kind]);
  const intersects = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  function hash(value) {
    let h = 2166136261;
    for (const c of String(value)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
    return h >>> 0;
  }
  function rgb(color) {
    const text = String(color || ''), hex = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(text);
    if (hex) {
      const value = hex[1].length === 3 ? hex[1].split('').map(c => c + c).join('') : hex[1];
      return [0, 2, 4].map(offset => parseInt(value.slice(offset, offset + 2), 16));
    }
    const match = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(text);
    return match ? match.slice(1, 4).map(Number) : null;
  }
  function tint(color, target, amount) {
    const parts = rgb(color);
    return parts ? 'rgb(' + parts.map((n, i) => Math.round(n * (1 - amount) + target[i] * amount)).join(',') + ')' : color;
  }
  function transparent(color) {
    const parts = rgb(color);
    return parts ? 'rgba(' + parts.join(',') + ',0)' : 'transparent';
  }
  function bodyRadius(node, position, projection, scale = 1) {
    const role = position?.role || 'planet', degree = Math.sqrt(Math.max(0, Number(node?.degree) || 0));
    const base = role === 'star' ? 12 + Math.min(4, degree * .45) : role === 'moon' ? 3.3 : 5.7 + Math.min(1.2, degree * .12);
    const perspective = clamp(projection?.perspective ?? 1, .2, 3);
    return clamp(base * perspective * Math.sqrt(clamp(scale, .01, 8)), role === 'moon' ? 2 : role === 'star' ? 6 : 3, role === 'star' ? 46 : role === 'moon' ? 15 : 24);
  }
  function dot(ctx, x, y, r) {
    ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
  }
  function allocateParticles(bodies,budget=1600){
    const allocations=bodies.map(body=>({body,wanted:body.position.role==='star'?100:body.position.role==='moon'?14:32}));
    const total=allocations.reduce((sum,item)=>sum+item.wanted,0);
    if(total<=budget){for(const item of allocations)item.body.particleCount=item.wanted;return;}
    const minimum=bodies.length<=budget?1:0;
    const available=Math.max(0,budget-minimum*bodies.length),weight=total-minimum*bodies.length;
    let remaining=budget;
    for(const item of allocations){const share=available*(item.wanted-minimum)/weight;item.body.particleCount=minimum+Math.floor(share);item.remainder=share-Math.floor(share);remaining-=item.body.particleCount;}
    allocations.sort((a,b)=>b.remainder-a.remainder||(a.body.node.id<b.body.node.id?-1:1));
    for(const item of allocations){if(remaining--<=0)break;item.body.particleCount++;}
  }
  function label(ctx, text, x, y, options, occupied, width, height) {
    const size = options.size || 10;
    ctx.font = (options.bold ? '600 ' : '') + size + 'px ' + FONT;
    const textWidth = ctx.measureText(text).width;
    for (const offset of options.offsets || [0, 15, -15]) {
      const yy = y + offset;
      const box = {left:x - textWidth / 2 - 4, right:x + textWidth / 2 + 4, top:yy - size - 3, bottom:yy + 3};
      if (box.left < 8 || box.right > width - 8 || box.top < 9 || box.bottom > height - 86 || occupied.some(other => intersects(box, other))) continue;
      occupied.push(box); ctx.globalAlpha = options.alpha ?? 1; ctx.fillStyle = options.color;
      ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'; ctx.fillText(text, x, yy);
      return true;
    }
    return false;
  }
  function draw(ctx, opts = {}) {
    const math = root.EvaMemoryGalaxy;
    if (!math?.project || !math?.orbitPoint) return {bodies:[]};
    const scene = opts.scene || {}, view = opts.view || {}, camera = opts.camera || {};
    const width = Math.max(0, Number(camera.width) || 0), height = Math.max(0, Number(camera.height) || 0);
    const clearWidth = Math.max(width, Number(opts.clearWidth) || 0), clearHeight = Math.max(height, Number(opts.clearHeight) || 0);
    ctx.clearRect(0, 0, clearWidth, clearHeight);
    if (!width || !height) return {bodies:[]};
    const particleMode=opts.particleBodies===true;
    const paint = particleMode?{...opts.paint,edge:'#7193b3',muted:'#a8c4db',text:'#edf6ff',highlight:'#dceeff',path:'#a4e5ff'}:opts.paint || {}, edgeColor = paint.edge || '#888888', muted = paint.muted || edgeColor;
    const textColor = paint.text || edgeColor, highlight = paint.highlight || textColor, pathColor = paint.path || highlight;
    const brightness = clamp(opts.edgeBrightness ?? .4, 0, 1), animate = opts.animate === true, immersive = opts.immersive === true;
    const time = Number.isFinite(Number(opts.time)) ? Number(opts.time) : 0;
    const selected = typeof opts.selected === 'object' ? opts.selected?.id : opts.selected;
    const hovered = typeof opts.hovered === 'object' ? opts.hovered?.id : opts.hovered;
    const center = typeof opts.localCenter === 'object' ? opts.localCenter?.id : opts.localCenter;
    const observedPrimary = !center && scene.systems?.find(system => system.id === opts.observedSystem)?.primary;
    const isObserved = id => Boolean(observedPrimary && (id === observedPrimary || scene.positions?.get(id)?.parent === observedPrimary));
    const active = observedPrimary && !isObserved(selected) ? hovered : selected || hovered, local = Boolean(center), near = new Set(active ? [active] : []);
    const bodies = [], byId = new Map(), occupied = [];
    let particles=0;
    for (const node of view.nodes || []) {
      const position = scene.positions?.get(node.id);
      if (!position) continue;
      const projection = math.project(position, camera);
      if (!projection.visible || !Number.isFinite(projection.x) || !Number.isFinite(projection.y)) continue;
      const radius = bodyRadius(node, position, projection, camera.scale);
      const body = {node, position, projection, radius};
      byId.set(node.id, body);
      if (projection.x >= -radius * 5 && projection.x <= width + radius * 5 && projection.y >= -radius * 5 && projection.y <= height + radius * 5) bodies.push(body);
    }
    // Larger projected depth is nearer to the camera. Stable tie-breaks avoid flicker.
    bodies.sort((a, b) => a.projection.depth - b.projection.depth || (a.node.id < b.node.id ? -1 : a.node.id > b.node.id ? 1 : 0));
    // Labels may move around a body, but never obscure another visible star or moon.
    for (const {projection:p,radius:r} of bodies) occupied.push({left:p.x-r-3,right:p.x+r+3,top:p.y-r-3,bottom:p.y+r+3});
    for(const box of opts.labelExclusions||[])if(['left','right','top','bottom'].every(key=>Number.isFinite(box?.[key])))occupied.push(box);
    const edges = (view.edges || []).filter(edge => ['stored_relation', 'provenance', 'goal_evidence'].includes(edge.kind) && byId.has(edge.source) && byId.has(edge.target));
    for (const edge of edges) if (active && (edge.source === active || edge.target === active)) {near.add(edge.source); near.add(edge.target);}
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, width, height); ctx.clip();
    if(particleMode){
      // The dust is projected from XYZ space too: camera rotation reveals its depth.
      const extent=Math.max(600,...bodies.map(body=>Math.hypot(body.position.x,body.position.y,body.position.z)));
      for(let i=0;i<640;i++){
        const seed=hash('dust:'+i),angle=(seed&65535)/65535*TAU,rad=Math.sqrt((seed>>>16)/65535)*extent*1.3;
        const point=math.project({x:Math.cos(angle)*rad,y:((hash('height:'+i)%1000)/1000-.5)*extent*.25,z:Math.sin(angle)*rad},camera);
        if(!point.visible||point.x<0||point.x>width||point.y<0||point.y>height)continue;
        ctx.globalAlpha=.12+(i%5)*.035;ctx.fillStyle=i%6===0?'#ffd8a0':'#94bde2';
        dot(ctx,point.x,point.y,clamp(point.perspective*.65,.35,1.4));particles++;
      }
    }
    // Quiet specks are a backdrop; they have no labels, hit targets or relation lines.
    ctx.fillStyle = muted;
    for (let i = 0; i < (immersive ? 150 : 64); i++) {
      const seed = hash('sky:' + i), x = ((seed & 65535) / 65535) * width, y = ((seed >>> 16) / 65535) * height;
      ctx.globalAlpha = (immersive ? .13 : .10) + (animate ? (immersive ? .075 : .05) * Math.sin(time / 2200 + i * 1.7) : immersive ? .04 : .025);
      dot(ctx, x, y, i % (immersive ? 13 : 11) === 0 ? (immersive ? 1.15 : .9) : (immersive ? .7 : .55));
    }
    if (opts.showOrbits !== false) for (const orbit of (scene.orbits || []).slice(0, 30)) {
      const projected = [];
      for (let i = 0; i <= 72; i++) projected.push(math.project(math.orbitPoint(orbit, i / 72 * TAU), camera));
      const centerDepth = math.project(orbit.center, camera).depth;
      ctx.strokeStyle = edgeColor; ctx.lineWidth = orbit.level > 1 ? .55 : .7; ctx.setLineDash([]);
      for (const front of [false, true]) {
        ctx.globalAlpha = front ? .21 : .10; ctx.beginPath();
        for (let i = 1; i < projected.length; i++) {
          const a = projected[i - 1], b = projected[i];
          if (!a.visible || !b.visible || ((a.depth + b.depth) / 2 >= centerDepth) !== front) continue;
          ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
        }
        ctx.stroke();
      }
    }
    const edgeLabels = [];
    for (let i = 0; i < edges.length; i++) {
      const edge = edges[i], aa = byId.get(edge.source), bb = byId.get(edge.target), a = aa.projection, b = bb.projection;
      const connected = Boolean(active && (edge.source === active || edge.target === active)), onPath = opts.pathEdges?.has(identity(edge));
      const color = onPath ? pathColor : edge.kind === 'provenance' ? paint.origin || muted : edgeColor;
      ctx.globalAlpha = onPath ? .95 : local ? connected ? .72 : .34 : active ? connected ? .62 : .035 : brightness * .45;
      ctx.strokeStyle = color; ctx.lineWidth = onPath ? 2.1 : connected ? 1.1 : .65;
      ctx.setLineDash(edge.kind === 'provenance' ? [3, 5] : []); ctx.beginPath();
      // Keep loop geometry consistent with the graph's relation hit test.
      if (edge.source === edge.target) ctx.arc(a.x + 10, a.y - 10, 13, 0, TAU);
      else {ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);}
      ctx.stroke(); ctx.setLineDash([]);
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
      if ((local || onPath) && length > aa.radius + bb.radius + 18) {
        const ux = dx / length, uy = dy / length, x = b.x - ux * (bb.radius + 5), y = b.y - uy * (bb.radius + 5);
        ctx.beginPath(); ctx.moveTo(x - ux * 6 - uy * 3, y - uy * 6 + ux * 3); ctx.lineTo(x, y); ctx.lineTo(x - ux * 6 + uy * 3, y - uy * 6 - ux * 3); ctx.stroke();
        if (opts.showLabels !== false && edgeLabels.length < 24 && length > 95) {
          const chars = Array.from(edge.relation === 'source_event' ? '来源事件' : edge.relation || '关系');
          const name = chars.length > 14 ? chars.slice(0, 14).join('') + '…' : chars.join('');
          edgeLabels.push({name,x:(a.x+b.x)/2,y:(a.y+b.y)/2-6,onPath});
        }
      }
      if (animate && i % 7 === 0 && length > 1 && (!active || connected)) {
        const phase = (time / 10000 + (hash(identity(edge)) % 1000) / 1000) % 1;
        ctx.globalAlpha = connected ? .8 : .45; ctx.fillStyle = aa.node.color || highlight;
        dot(ctx, a.x + dx * phase, a.y + dy * phase, 1.2);
      }
    }
    if(particleMode)allocateParticles(bodies);
    for (let bodyIndex=0;bodyIndex<bodies.length;bodyIndex++) {
      const body=bodies[bodyIndex];
      const {node, position, projection:p, radius:r} = body, color = node.color || highlight;
      const focused = node.id === active, isCenter = node.id === center, star = position.role === 'star';
      const alpha = active && !near.has(node.id) ? local ? .68 : .28 : 1;
      ctx.globalAlpha = alpha * (star ? .26 : focused ? .28 : .12);
      const halo = ctx.createRadialGradient(p.x, p.y, r * .3, p.x, p.y, r * (star ? (immersive ? 6.5 : 5) : (immersive ? 3.8 : 3)));
      halo.addColorStop(0, color); halo.addColorStop(1, transparent(color)); ctx.fillStyle = halo;
      dot(ctx, p.x, p.y, r * (star ? (immersive ? 6.5 : 5) : (immersive ? 3.8 : 3)));
      if (star) {
        ctx.globalAlpha = alpha * .48; ctx.strokeStyle = color; ctx.lineWidth = .6;
        ctx.beginPath();
        for (let ray = 0; ray < 16; ray++) {
          const angle = ray / 16 * TAU, start = r * 1.23, end = r * (ray % 2 ? 1.62 : 1.95);
          ctx.moveTo(p.x + Math.cos(angle) * start, p.y + Math.sin(angle) * start); ctx.lineTo(p.x + Math.cos(angle) * end, p.y + Math.sin(angle) * end);
        }
        ctx.stroke();
      }
      if(particleMode){
        const count=body.particleCount;
        const worldRadius=r/Math.max(.001,p.perspective*(Number(camera.scale)||1)),cloud=[];
        for(let i=0;i<count;i++){
          const seed=hash(node.id+':particle:'+i),azimuth=(seed&65535)/65535*TAU,vertical=(seed>>>16)/65535*2-1;
          const radial=Math.sqrt(1-vertical*vertical),shell=.3+.7*Math.cbrt((hash(node.id+':radius:'+i)%10000)/10000);
          const point=math.project({x:position.x+Math.cos(azimuth)*radial*shell*worldRadius,y:position.y+vertical*shell*worldRadius,z:position.z+Math.sin(azimuth)*radial*shell*worldRadius},camera);
          if(point.visible)cloud.push(point);
        }
        cloud.sort((a,b)=>a.depth-b.depth);
        for(const point of cloud){
          ctx.globalAlpha=alpha*(point.depth<p.depth?.45:.95);ctx.fillStyle=tint(color,[255,255,255],point.depth<p.depth?.2:.65);
          dot(ctx,point.x,point.y,clamp(r/(star?13:7),.55,2));particles++;
        }
      }else{
      const sphere = ctx.createRadialGradient(p.x - r * .32, p.y - r * .35, r * .04, p.x + r * .12, p.y + r * .18, r * 1.18);
      sphere.addColorStop(0, tint(color, [255, 255, 255], star ? .72 : .55));
      sphere.addColorStop(.38, color); sphere.addColorStop(1, tint(color, [12, 16, 24], star ? .22 : .58));
      ctx.globalAlpha = alpha; ctx.fillStyle = sphere; dot(ctx, p.x, p.y, r);
      ctx.globalAlpha = alpha * .45; ctx.strokeStyle = tint(color, [255, 255, 255], .3); ctx.lineWidth = .65; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.stroke();
      }
      if (focused) {
        ctx.globalAlpha = 1; ctx.strokeStyle = pathColor; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(p.x, p.y, r + 6, 0, TAU); ctx.stroke();
      }
      if (isCenter) {
        ctx.globalAlpha = .75; ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.arc(p.x, p.y, r + 12, 0, TAU); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    if (opts.showLabels !== false) {
      // Selection wins label space, followed by system primaries and nearby records.
      const candidates = bodies.filter(body => body.node.id === active || body.position.role === 'star' || isObserved(body.node.id) || local || (active && near.has(body.node.id)) || (camera.scale > 1.2 && body.node.degree > 2));
      candidates.sort((a, b) => Number(b.node.id === active) - Number(a.node.id === active) || Number(b.position.role === 'star') - Number(a.position.role === 'star') || (b.node.degree || 0) - (a.node.degree || 0) || b.projection.depth - a.projection.depth);
      let count = 0;
      function recordLabel(body){
        const {node, projection:p, radius:r} = body, focused = node.id === active;
        const chars = Array.from(String(node.label || node.title || node.id));
        const name = chars.length > 24 ? chars.slice(0, 24).join('') + '…' : chars.join('');
        return label(ctx, name, p.x, p.y + r + 19, {color:focused ? textColor : muted, bold:focused, size:focused ? 11 : 10, alpha:active && !near.has(node.id) ? local ? .85 : .5 : 1, offsets:[0, 14, -r * 2 - 27]}, occupied, width, height);
      }
      function systemLabels(){
        if(local)return;
        for (const system of (scene.systems || []).slice(0, 9)) {
          const primary=byId.get(system.primary);if(!primary||!primary.projection.visible)continue;
          label(ctx,system.id+' · '+(system.label||'记忆星系'),primary.projection.x,primary.projection.y-primary.radius-15,{color:particleMode?tint(primary.node.color||muted,[255,255,255],.4):primary.node.color||muted,size:10,bold:true,offsets:particleMode?[0,-15,-60,-90,primary.radius*2+35,primary.radius*2+65,primary.radius*2+95]:[0,-15]},occupied,width,height);
        }
      }
      const priority=particleMode&&!local&&candidates.find(body=>body.node.id===active);
      if(priority&&recordLabel(priority))count++;
      if(particleMode)systemLabels();
      for (const body of candidates) {
        if (count >= 35) break;
        if(body===priority)continue;
        if(recordLabel(body))count++;
      }
      if(!particleMode)systemLabels();
      // Record names take precedence over relationship annotations in crowded scenes.
      for (const item of edgeLabels.sort((a,b)=>Number(b.onPath)-Number(a.onPath)))
        label(ctx,item.name,item.x,item.y,{color:item.onPath?pathColor:muted,alpha:item.onPath?1:.8,offsets:[0,-15,15]},occupied,width,height);
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.restore();
    return {bodies,particles};
  }
  return {draw, bodyRadius};
});
