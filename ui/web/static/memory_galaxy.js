/* Display geometry only. Orbits do not create memory relations or rank credibility. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.EvaMemoryGalaxy = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const TAU = Math.PI * 2;
  const TIER_ORDER = ['S4', 'S3', 'S2', 'S1', 'S5', 'G', 'R', 'V', 'E', 'A', 'I'];
  const TIER_LABELS = {S1:'会话记忆', S2:'工作记忆', S3:'长期记忆', S4:'世界模型', S5:'事件档案', G:'业务目标', R:'请求回执', V:'文件检查', E:'执行记录', A:'Agent 行动', I:'历史输入引用'};
  const TIER_SPEED = {S1:.11,S2:.075,S3:.04,S4:.022,S5:.016,G:.06,R:.09,V:.05,E:.035,A:.025,I:.02};
  const TIER_RADIUS = {S4:0,S3:420,S2:650,S1:880,S5:1120,G:450,R:670,V:890,E:1110,A:1330,I:1540};
  const zero = () => ({x:0, y:0, z:0});
  const finite = (value, fallback = 0, limit = 1e6) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(-limit, Math.min(limit, n)) : fallback;
  };
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  function hash(text) {
    let result = 2166136261;
    for (const char of String(text)) result = Math.imul(result ^ char.charCodeAt(0), 16777619);
    return result >>> 0;
  }
  const phaseFor = text => hash(text) / 4294967296 * TAU;
  const compareId = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  function rotate(point = {}, angles = {}) {
    const x = finite(point?.x), y = finite(point?.y), z = finite(point?.z);
    const yaw = finite(angles?.yaw) % TAU, pitch = finite(angles?.pitch) % TAU;
    const cx = Math.cos(yaw), sx = Math.sin(yaw), cy = Math.cos(pitch), sy = Math.sin(pitch);
    const rx = x * cx + z * sx, rz = -x * sx + z * cx;
    return {x:rx, y:y * cy - rz * sy, z:y * sy + rz * cy};
  }
  function project(point = {}, camera = {}) {
    const rotated = rotate(point, camera);
    const distance = clamp(finite(camera?.distance, 1800), 1, 100000);
    const nearPlane = Math.max(1, distance / 8), gap = distance - rotated.z;
    const perspective = clamp(distance / Math.max(nearPlane, gap), .01, 8);
    const scale = clamp(finite(camera?.scale, 1), .01, 8);
    const width = clamp(finite(camera?.width, 900), 0, 100000), height = clamp(finite(camera?.height, 700), 0, 100000);
    return {
      x:finite(width / 2 + finite(camera?.panX) + rotated.x * perspective * scale),
      y:finite(height / 2 + finite(camera?.panY) + rotated.y * perspective * scale),
      depth:rotated.z, perspective, visible:gap > nearPlane
    };
  }
  function orbitPoint(orbit = {}, phase = 0) {
    const radius = clamp(finite(orbit?.radius), 0, 2000), angle = finite(phase) % TAU;
    const offset = rotate({x:Math.cos(angle) * radius, y:Math.sin(angle) * radius, z:0}, orbit?.tilt);
    return {x:finite(orbit?.center?.x) + offset.x, y:finite(orbit?.center?.y) + offset.y, z:finite(orbit?.center?.z) + offset.z};
  }
  function nodePosition(point, role, level, parent = null, orbit = null, phase = 0) {
    return {...point, role, level, parent, orbitRadius:orbit?.radius || 0, orbitTilt:orbit?.tilt || {yaw:0, pitch:0}, phase};
  }
  function rings(nodes, center, parent, level, seed, positions, orbits, motions, options = {}) {
    let offset = 0, shell = 0;
    const base = options.base ?? 95, increment = options.increment ?? 36;
    const capacity = options.capacity ?? 12, growth = options.growth ?? 6;
    while (offset < nodes.length) {
      const count = Math.min(capacity + shell * growth, nodes.length - offset);
      const radius = base + shell * increment, phase = phaseFor(seed + ':' + shell);
      const orbit = {
        center:{x:center.x, y:center.y, z:center.z}, radius,
        tilt:{yaw:Math.sin(phase) * .55, pitch:.25 + Math.cos(phase) * .45},
        level, parent, label:shell === 0 ? options.label || '' : '',speed:(TIER_SPEED[nodes[offset]?.tier]||.04)/Math.sqrt(shell+1)
      };
      orbits.push(orbit);
      for (let i = 0; i < count; i++) {
        const node = nodes[offset + i], angle = phase + i / count * TAU;
        positions.set(node.id, nodePosition(orbitPoint(orbit, angle), level > 1 ? 'moon' : 'planet', level, parent, orbit, angle));
        motions.set(node.id,{orbit,speed:(TIER_SPEED[node.tier]||.04)*(1+(level-1)*.55)/Math.sqrt(shell+1)});
      }
      offset += count;
      shell++;
    }
  }
  function layout(view = {}, options = {}) {
    // Sort before bounding, so source array order never changes the visible geometry.
    const unique = new Map();
    for (const node of view.nodes || []) if (node && typeof node.id === 'string' && !unique.has(node.id)) unique.set(node.id, node);
    const nodes = [...unique.values()].sort(compareId).slice(0, 600);
    const nodeMap = new Map(nodes.map(node => [node.id, node]));
    const positions = new Map(), orbits = [], systems = [], motions = new Map();
    if (!nodes.length) return {positions, orbits, systems, motions, maxRadius:0};
    const centerId = typeof options.center === 'string' ? options.center : options.center?.id;
    if (options.local && nodeMap.has(centerId)) {
      const centerNode = nodeMap.get(centerId), center = zero();
      positions.set(centerId, nodePosition(center, 'star', 0));
      systems.push({id:centerId, label:centerNode.label || centerNode.title || centerId, center, primary:centerId});
      const levels = new Map([[centerId, 0]]), groups = new Map(), detached = [];
      for (const node of nodes) if (node.id !== centerId) {
        const raw = view.distances?.get(node.id), level = Number.isInteger(raw) && raw >= 1 && raw <= 3 ? raw : null;
        if (level !== null) levels.set(node.id, level);
        else detached.push(node);
      }
      for (const node of nodes) if (node.id !== centerId && levels.has(node.id)) {
        const level = levels.get(node.id), step = view.parents?.get(node.id), parent = step?.from;
        // A missing or inconsistent BFS parent must not manufacture a relationship.
        if (!nodeMap.has(parent) || levels.get(parent) !== level - 1) {detached.push(node); continue;}
        const key = level + ':' + parent;
        if (!groups.has(key)) groups.set(key, {parent, level, nodes:[]});
        groups.get(key).nodes.push(node);
      }
      for (let level = 1; level <= 3; level++) for (const group of groups.values()) if (group.level === level) {
        const parent = positions.get(group.parent);
        if (!parent) {detached.push(...group.nodes); continue;}
        const compact = level > 1;
        rings(group.nodes, parent, group.parent, level, group.parent + ':' + level, positions, orbits, motions, {
          base:compact ? (level === 2 ? 44 : 26) : 125,
          increment:compact ? (level === 2 ? 18 : 10) : 42,
          capacity:compact ? 8 : 10, growth:compact ? 4 : 6,
          label:level === 1 ? '一跳关系' : level === 2 ? '二跳关系' : '三跳关系'
        });
      }
      rings(detached.sort(compareId), center, null, 1, centerId + ':unlinked', positions, orbits, motions, {base:680, increment:10, label:'未提供关系路径'});
    } else {
      const degree = new Map(nodes.map(node => [node.id, 0]));
      for (const edge of view.edges || []) if (degree.has(edge.source) && degree.has(edge.target)) {
        degree.set(edge.source, degree.get(edge.source) + 1);
        degree.set(edge.target, degree.get(edge.target) + 1);
      }
      const groups = new Map();
      for (const node of nodes) {
        const tier = TIER_ORDER.includes(node.tier) ? node.tier : 'other';
        if (!groups.has(tier)) groups.set(tier, []);
        groups.get(tier).push(node);
      }
      const tierIndex=tier=>TIER_ORDER.includes(tier)?TIER_ORDER.indexOf(tier):TIER_ORDER.length;
      const tiers = [...groups.keys()].sort((a, b) => tierIndex(a) - tierIndex(b));
      tiers.forEach((tier, index) => {
        const members = groups.get(tier), ranked = [...members].sort((a, b) => degree.get(b.id) - degree.get(a.id) || compareId(a, b));
        const primary = ranked[0], angle = phaseFor('system:'+tier)+Math.max(0,TIER_ORDER.indexOf(tier))*2.3999632297;
        const orbit=index===0?null:{center:zero(),radius:TIER_RADIUS[tier]||760,tilt:{yaw:Math.sin(angle)*.35,pitch:.2+Math.cos(angle)*.25},level:0,parent:null,label:TIER_LABELS[tier]||'其他记录',speed:(TIER_SPEED[tier]||.04)*.35};
        const center = orbit?orbitPoint(orbit,angle):zero();
        positions.set(primary.id, nodePosition(center, 'star', 0,null,orbit,angle));
        if(orbit){orbits.push(orbit);motions.set(primary.id,{orbit,speed:orbit.speed});}
        systems.push({id:tier, label:TIER_LABELS[tier] || '其他记录', center, primary:primary.id});
        rings(members.filter(node => node.id !== primary.id), center, primary.id, 1, tier, positions, orbits, motions, {label:TIER_LABELS[tier] || '其他记录'});
      });
    }
    let maxRadius = 0;
    for (const point of positions.values()) maxRadius = Math.max(maxRadius, Math.hypot(point.x, point.y, point.z));
    for (const orbit of orbits) maxRadius = Math.max(maxRadius, Math.hypot(orbit.center.x, orbit.center.y, orbit.center.z) + orbit.radius);
    return {positions, orbits, systems, motions, maxRadius};
  }
  function sample(scene={},seconds=0){
    if(!scene.motions?.size||!seconds)return scene;
    const elapsed=finite(seconds,0,1e9),positions=new Map();
    const ordered=[...(scene.positions||[])].sort((a,b)=>a[1].level-b[1].level);
    for(const [id,point] of ordered){
      const motion=scene.motions.get(id);
      if(!motion){positions.set(id,point);continue;}
      const center=motion.orbit.parent?positions.get(motion.orbit.parent)||motion.orbit.center:motion.orbit.center;
      const phase=point.phase+elapsed*motion.speed;
      positions.set(id,{...point,...orbitPoint({...motion.orbit,center},phase)});
    }
    const orbits=(scene.orbits||[]).map(orbit=>orbit.parent?{...orbit,center:positions.get(orbit.parent)||orbit.center}:orbit);
    const systems=(scene.systems||[]).map(system=>({...system,center:positions.get(system.primary)||system.center}));
    return {...scene,positions,orbits,systems};
  }
  return {layout, sample, rotate, project, orbitPoint};
});
