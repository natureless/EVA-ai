/* Persisted memory or goal evidence only. Animation has no cognitive semantics. */
(function (root) {
  'use strict';
  const TIERS = {S1:['会话记忆','var(--ui-tier-s1)'], S2:['工作记忆','var(--ui-tier-s2)'], S3:['长期记忆','var(--ui-tier-s3)'], S4:['世界模型','var(--ui-tier-s4)'], S5:['事件档案','var(--ui-tier-s5)']};
  Object.assign(TIERS,{G:['业务目标','var(--ui-tier-s1)'],R:['请求回执','var(--ui-tier-s3)'],V:['文件检查','var(--ui-tier-s2)'],E:['执行记录','var(--ui-tier-s4)']});
  Object.assign(TIERS,{A:['Agent 行动','var(--ui-tier-s5)'],I:['历史输入引用','var(--ui-tier-s2)']});
  const GOAL_TIERS=new Set(['G','R','V','E','A','I']);
  const EDGE_KINDS=['stored_relation','provenance','goal_evidence','action_input'];
  const STATUS={queued:'排队中',active:'进行中',pending_verification:'目标待验证',completed:'指定条件已通过',failed:'失败',cancelled:'已取消',expired:'已过期',interrupted:'已中断',succeeded:'请求已处理',rejected:'请求被拒绝',outcome_unknown:'请求结果未知',passed:'指定文件检查已通过',mismatch:'指定文件不匹配',unknown:'检查结果未知'};
  function statusLabel(status){return STATUS[status]||status||'未记录';}
  Object.assign(STATUS,{episode_pending:'执行记录：待处理',episode_succeeded:'执行记录：成功',episode_failed:'执行记录：失败',episode_unknown:'执行记录：结果未知'});
  Object.assign(STATUS,{action_pending:'Agent 意图已保存',action_executing:'Agent 已开始，尚无返回',action_completed:'Agent 返回成功（非目标认证）',action_unknown:'Agent 结果未知'});
  const INPUT_RELATIONS={request_agent_intent:'请求的 Agent 行动',used_world_context:'使用世界上下文',used_memory_view:'使用记忆检索视图',used_memory_input:'使用输入记忆'};
  function episodeReferenceLabel(reference){
    if(!reference)return '未提供引用状态';
    if(reference.status==='available')return '已关联 '+reference.episode_id;
    if(reference.status==='not_recorded')return reference.reason==='store_missing'?'存储中未接入 Episode':'来源事件未记录 Episode';
    return '引用不可用（格式、身份或读取异常）；未建立连接';
  }
  function evidenceSummary(node){
    const r=node.record||{};
    if(node.tier==='A')return `请求：${r.task_id}\n来源事件：${r.event_id}\n行动：${r.action_id}\n派发 revision：${r.state_version}\n父行动：${r.parent_action_id}\n上下文 SHA-256：${r.context_hash}\n任务 SHA-256：${r.task_hash}\n开始时间：${r.observation?.started_at||'未记录'}\n返回时间：${r.observation?.finished_at||'未取得'}\n封存时间：${r.observation?.sealed_at||'未记录'}\n输入引用只说明执行使用的依据，不证明判断或目标成功。`;
    if(node.tier==='I')return (r.memory_id?`输入记忆：${r.tier}:${r.memory_id}\n输入记录 SHA-256：${r.integrity_hash}\n`:'')+`视图 scope：${(r.memory_view||r).scope}\n视图 revision：${(r.memory_view||r).revision}\n视图 SHA-256：${(r.memory_view||r).integrity_hash}\n仅保存历史引用与哈希；未读取当前正文或校验当前记录是否一致。`;
    const tools=r.tool_receipts;
    const toolSummary=!tools||tools.status==='not_applicable'?'':tools.status==='available'
      ? '\n工具收据（处理器观测，不证明外部副作用）：\n'+tools.receipts.map(t=>`${t.tool_id} · ${{completed:'处理器返回成功',unknown:'结果未知',started:'已开始，尚无返回'}[t.status]||t.status}\n收据：${t.receipt_id}\n请求哈希：${t.request_hash}\n返回时间：${t.finished_at||'未取得'}${t.observation_kind==='seal_unknown'?'（封存时结果未知，不是完成时间）':''}${toolObservationSummary(t.reconciliation)}`).join('\n\n')
      : '\n工具收据：'+(tools.status==='not_recorded'?'未记录':'不可用（身份、格式或读取异常）');
    const stateReference=r.state_reference_kind==='world_snapshot_unversioned'
      ? `世界快照：版本未知，仅记录内容哈希\n处理前：${r.state_before?.integrity_hash||'未记录'}\n处理后：${r.state_after?.integrity_hash||'未记录'}`
      : `状态 revision：${r.state_before?.version??'未记录'} → ${r.state_after?.version??'未记录'}`;
    if(node.tier==='G')return `目标版本：${r.version}\n请求：${r.task_id}\n来源事件：${r.source_event_id}\n成功条件：${JSON.stringify(r.success_conditions)}\n截止时间：${r.deadline||'未设置'}\n已存检查：${node.verification_count||0} 次${node.history_truncated?'（仅显示最近 5 次）':''}\n执行记录：${episodeReferenceLabel(r.episode_reference)}`;
    if(node.tier==='R')return `请求：${r.task_id}\n事件：${r.event_id}\n处理结果：${statusLabel(r.terminal_state)}\n请求成功仍需独立的业务检查。`;
    if(node.tier==='V')return `检查时间：${r.observed_at}\n目标版本：${r.base_goal_version} → ${r.committed_goal_version}\n检查范围：指定文件内容采样\n`+(r.files||[]).map(f=>`${f.path}\n结果：${{match:'匹配',mismatch:'不匹配',missing:'文件缺失',unknown:'未知'}[f.outcome]||f.outcome}${f.reason?' · '+f.reason:''}\n预期 SHA-256：${f.expected_sha256}\n观测 SHA-256：${f.observed_sha256||'未取得'}`).join('\n\n');
    if(node.tier==='E')return `执行记录：${r.episode_id}\n来源事件：${r.event_id}\n开始时间：${r.started_at}\n${r.completion_kind?'记录封存时间':'结束时间'}：${r.completed_at||'未记录'}${r.completion_kind==='recovery_unknown'?'（重启恢复，执行结果未知）':''}\n${stateReference}\n策略版本：${r.policy_version||'未记录'}\n规划版本：${r.strategy_version||'未记录'}\n结果回执：${r.result_receipt_id||'未记录'}\n动作：${r.action_count||0} 个${r.actions_truncated?'（仅展示前 16 个）':''}${r.fields_truncated?'\n部分长字段已截断':''}${toolSummary}\n查看只读取引用，不重放动作；执行成功不等于目标完成。`;
    return '';
  }
  function toolObservationSummary(value){
    if(!value)return '';
    if(value.status!=='available')return '\n独立文件核验：'+(value.status==='unsupported'?'没有写入前固定的检查描述':'记录不可用');
    return `\n独立文件核验：${value.count} 次${value.truncated?'（展示最近 3 次）':''}\n`+(value.records||[]).map(r=>`采样 ${r.sequence} · ${statusLabel(r.outcome)} · ${r.observed_at}\n`+(r.files||[]).map(f=>`${f.path} · ${f.outcome}\n预期哈希：${f.expected_sha256}\n采样哈希：${f.observed_sha256||'未取得'}`).join('\n')).join('\n')+'\n采样不证明写入因果，不改变原始执行结果或目标状态。';
  }
  const ORIGINS = {verified_fact:'已验证事实（记录标注）', user_statement:'用户陈述', working_model:'工作模型', hypothesis:'假设', assistant_inference:'助手推断', tool_observation:'工具观察', simulation:'模拟结果', unknown:'未知 / 历史未标注'};
  function hash(text) { let h=2166136261; for (const c of text) h=Math.imul(h^c.charCodeAt(0),16777619); return h>>>0; }
  function edgeIdentity(edge){return JSON.stringify([edge.source,edge.target,edge.relation,edge.kind]);}
  function relationKey(edge){return JSON.stringify([edge.kind,edge.relation??null]);}
  function relationLabel(key){const [kind,name]=JSON.parse(key);return `${kind==='provenance'&&name==='source_event'?'来源事件':INPUT_RELATIONS[name]||name||'未标注'} · ${kind==='action_input'?'历史输入引用':kind==='goal_evidence'?'目标证据引用':kind==='provenance'?'来源引用':'存储关系'}`;}
  function relationChoices(edges,selected=''){
    const counts=new Map();for(const edge of edges){const key=relationKey(edge);counts.set(key,(counts.get(key)||0)+1);}
    if(selected&&!counts.has(selected))counts.set(selected,0);
    return [...counts].sort(([a],[b])=>a<b?-1:a>b?1:0).map(([key,count])=>({key,label:relationLabel(key),count}));
  }
  function hitEdges(edges,project,x,y,tolerance=6){
    const matches=[],seen=new Set();
    for(const edge of edges){
      const a=project(edge.a),b=project(edge.b),dx=b.x-a.x,dy=b.y-a.y;
      if(a.visible===false||b.visible===false)continue;
      let distance;
      if(edge.source===edge.target)distance=Math.abs(Math.hypot(x-a.x-10,y-a.y+10)-13);
      else{
        const length=dx*dx+dy*dy;if(length<.01)continue;
        const t=Math.max(0,Math.min(1,((x-a.x)*dx+(y-a.y)*dy)/length));
        distance=Math.hypot(x-a.x-t*dx,y-a.y-t*dy);
      }
      const key=edgeIdentity(edge);
      if(distance<=tolerance&&!seen.has(key)){matches.push({edge,distance});seen.add(key);}
    }
    return matches.sort((a,b)=>a.distance-b.distance).map(item=>item.edge);
  }
  function buildGraph(data, previous=new Map()) {
    const seen=new Set();
    const nodes=(data.nodes||[]).filter(n=>TIERS[n.tier] && typeof n.id==='string' && !seen.has(n.id) && seen.add(n.id)).slice(0,600).map(n=>{
      const h=hash(n.id), angle=h/4294967296*Math.PI*2, radius=50+Math.sqrt((hash(n.id+'r')%10000)/10000)*390;
      const old=previous.get(n.id);
      return {...n, x:old?.x ?? Math.cos(angle)*radius, y:old?.y ?? Math.sin(angle)*radius*.85, vx:0, vy:0, degree:0, color:TIERS[n.tier][1]};
    });
    const map=new Map(nodes.map(n=>[n.id,n]));
    const edges=(data.edges||[]).filter(e=>map.has(e.source)&&map.has(e.target)&&EDGE_KINDS.includes(e.kind)).slice(0,3000).map(e=>({...e,a:map.get(e.source),b:map.get(e.target)}));
    const links=new Set(), springs=[];
    for(const e of edges){e.a.degree++;e.b.degree++;const k=[e.source,e.target].sort().join('\u0000');if(!links.has(k)&&e.a!==e.b){links.add(k);springs.push(e);}}
    return {nodes,edges,map,springs};
  }
  function visibleGraph(model, options) {
    let nodes=model.nodes.filter(n=>options.tiers.has(n.tier));
    let ids=new Set(nodes.map(n=>n.id));
    let edges=model.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)&&(options.origins||e.kind!=='provenance'));
    const center=options.center||options.selected, distances=new Map(),parents=new Map();
    if(options.local && center){
      const depth=Math.max(1,Math.min(3,Number(options.depth)||1));
      if(options.relationKind && options.relationKind!=='all')edges=edges.filter(e=>e.kind===options.relationKind);
      if(options.relationName)edges=edges.filter(e=>relationKey(e)===options.relationName);
      if(ids.has(center))distances.set(center,0);
      const adjacency=new Map(nodes.map(n=>[n.id,[]]));
      for(const e of edges){
        if(options.direction!=='incoming')adjacency.get(e.source).push({id:e.target,edge:e});
        if(options.direction!=='outgoing')adjacency.get(e.target).push({id:e.source,edge:e});
      }
      // Stable tie-breaking chooses one shortest path, never a strongest/most credible path.
      for(const links of adjacency.values())links.sort((a,b)=>{const x=edgeIdentity(a.edge),y=edgeIdentity(b.edge);return x<y?-1:x>y?1:0;});
      let frontier=distances.has(center)?[center]:[];
      for(let level=1;level<=depth && frontier.length;level++){
        const next=[];
        for(const id of frontier)for(const neighbor of adjacency.get(id))if(!distances.has(neighbor.id)){distances.set(neighbor.id,level);parents.set(neighbor.id,{from:id,to:neighbor.id,edge:neighbor.edge});next.push(neighbor.id);}
        frontier=next;
      }
      nodes=nodes.filter(n=>distances.has(n.id)); ids=new Set(nodes.map(n=>n.id));
      edges=edges.filter(e=>ids.has(e.source)&&ids.has(e.target));
    }
    if(!options.orphans){const linked=new Set(edges.flatMap(e=>[e.source,e.target]));nodes=nodes.filter(n=>linked.has(n.id)||(options.local&&n.id===center));}
    return {nodes,edges,distances,parents};
  }
  function connectionPath(view,center,target){
    if(!view.distances?.has(center)||!view.distances.has(target))return null;
    const path=[],seen=new Set();let id=target;
    while(id!==center){
      const step=view.parents?.get(id);if(!step||seen.has(id))return null;
      seen.add(id);path.unshift(step);id=step.from;
    }
    return path;
  }
  function mergeNeighborhood(current, incoming, nodeLimit=600, edgeLimit=3000){
    const nodes=new Map(current.nodes.map(n=>[n.id,n])),key=e=>JSON.stringify([e.source,e.target,e.relation,e.kind]);
    const edges=new Map(current.edges.map(e=>[key(e),e])),available=new Map(incoming.nodes.map(n=>[n.id,n]));
    const beforeNodes=nodes.size,beforeEdges=edges.size;let omitted=0;
    for(const edge of incoming.edges){
      const identity=key(edge),missing=[...new Set([edge.source,edge.target])].filter(id=>!nodes.has(id));
      if(!['stored_relation','provenance'].includes(edge.kind)||missing.some(id=>!available.has(id))){omitted++;continue;}
      if(nodes.size+missing.length>nodeLimit||(!edges.has(identity)&&edges.size>=edgeLimit)){omitted++;continue;}
      for(const id of [edge.source,edge.target])if(available.has(id))nodes.set(id,available.get(id));
      edges.set(identity,edge);
    }
    return {nodes:[...nodes.values()],edges:[...edges.values()],addedNodes:nodes.size-beforeNodes,addedEdges:edges.size-beforeEdges,omitted};
  }
  function mergeActionInputs(current,incoming,anchor,nodeLimit=600,edgeLimit=3000){
    const r=anchor?.record||{},binding=incoming?.binding;
    if(!['G','R'].includes(anchor?.tier)||!binding||binding.task_id!==r.task_id||binding.event_id!==(r.source_event_id||r.event_id)||incoming.schema_version!==1||incoming.scope?.historical_references_only!==true)throw new Error('输入引用与当前请求不一致');
    const actionId='A:'+binding.action_id,action=incoming.nodes?.find(n=>n.id===actionId&&n.tier==='A');
    if(!action||action.record?.action_id!==binding.action_id||action.record?.task_id!==binding.task_id||action.record?.event_id!==binding.event_id||action.record?.state_version!==binding.state_version||action.record?.request_hash!==binding.request_hash)throw new Error('行动身份不一致');
    const ids=new Set(incoming.nodes.map(n=>n.id));
    if(incoming.nodes.length>13||ids.size!==incoming.nodes.length||incoming.edges.length>12||incoming.nodes.some(n=>n.id!==actionId&&n.tier!=='I')||incoming.edges.some(e=>e.source!==actionId||!ids.has(e.target)||e.kind!=='action_input'||!['used_world_context','used_memory_view','used_memory_input'].includes(e.relation)))throw new Error('输入图谱格式不完整');
    const nodes=new Map(current.nodes.map(n=>[n.id,n])),edges=new Map(current.edges.map(e=>[edgeIdentity(e),e]));
    for(const node of incoming.nodes)nodes.set(node.id,node);
    for(const edge of [...incoming.edges,{source:anchor.id,target:actionId,kind:'action_input',relation:'request_agent_intent'}])edges.set(edgeIdentity(edge),edge);
    if(nodes.size>nodeLimit||edges.size>edgeLimit)throw new Error('已达图谱容量，请缩小目标范围后重试');
    return {nodes:[...nodes.values()],edges:[...edges.values()]};
  }
  function step(model, fixed=null) {
    const nodes=model.nodes;
    for(let i=0;i<nodes.length;i++){
      const a=nodes[i];
      for(let j=i+1;j<nodes.length;j++){
        const b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y;
        if(Math.abs(dx)+Math.abs(dy)<.01){dx=.1;dy=.1;}
        const d2=dx*dx+dy*dy+80, f=180/(d2*Math.sqrt(d2));
        a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;
      }
    }
    for(const e of model.springs){
      const dx=e.b.x-e.a.x,dy=e.b.y-e.a.y,d=Math.hypot(dx,dy)||1;
      const desired=65+Math.min(80,Math.sqrt(Math.max(e.a.degree,e.b.degree))*7);
      const force=(d-desired)*.007/d;
      e.a.vx+=dx*force;e.a.vy+=dy*force;e.b.vx-=dx*force;e.b.vy-=dy*force;
    }
    for(const n of nodes){
      n.vx=(n.vx-n.x*.00045)*.82;n.vy=(n.vy-n.y*.00045)*.82;
      if(n.id!==fixed){n.x+=Math.max(-7,Math.min(7,n.vx));n.y+=Math.max(-7,Math.min(7,n.vy));}
      else{n.vx=0;n.vy=0;}
    }
  }
  root.EvaMemoryGraph={buildGraph,visibleGraph,connectionPath,mergeNeighborhood,mergeActionInputs,step,hash,hitEdges,edgeIdentity,relationKey,relationChoices,evidenceSummary,statusLabel};
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);
  if(!$('memoryCanvas'))return;
  const canvas=$('memoryCanvas'),ctx=canvas.getContext('2d'),workspace=document.querySelector('.workspace');
  let data=null, model=buildGraph({}), view={nodes:[],edges:[]}, selected=null, hovered=null, local=false;
  let localCenter=null,localHistory=[],globalCamera=null,detailsCollapsed=false;
  let filterPanelOpen=root.innerWidth>700;
  $('localFilterPanel').open=filterPanelOpen;
  let width=0,height=0,viewWidth=0,viewHeight=0,scale=.8,panX=0,panY=0,drag=null,steps=0,raf=0,lastFrame=0;
  const galaxy=root.EvaMemoryGalaxy,galaxyRenderer=root.EvaMemoryGalaxyRenderer;
  let dimension=$('graphDimension').value==='2d'||!galaxy||!galaxyRenderer?'2d':'3d';
  let yaw=-.45,pitch=.55,galaxyTarget='',scene={positions:new Map(),orbits:[],systems:[]};
  let galaxyMenuSignature='';
  let cameraFlight=null;
  let baseScene=scene,orbitSeconds=0,orbitFrame=null,inspectionPlayback=false;
  function orbitRunning(){return is3d()&&$('orbitMotion').checked&&!reduceMotion.matches&&(!selected||inspectionPlayback)&&!hovered&&!drag&&!pinch&&!cameraFlight&&!suspended()&&view.nodes.length>0;}
  function syncOrbitControls(){
    const enabled=$('orbitMotion').checked,reading=Boolean(selected&&!inspectionPlayback);
    $('orbitPlayback').hidden=!is3d();$('orbitPlayback').disabled=!is3d()||reduceMotion.matches||!view.nodes.length;
    const label=!enabled?'开启运转':reading?'继续运转':'暂停运转';
    if($('orbitPlayback').textContent!==label)text('orbitPlayback',label);
    text('orbitSpeedValue',(Math.max(.1,Math.min(2,Number($('orbitSpeed').value)/100||1))).toFixed(2)+'×');
  }
  function toggleOrbitPlayback(){
    if(!is3d()||reduceMotion.matches||suspended()||!view.nodes.length)return;
    const resume=!$('orbitMotion').checked||Boolean(selected&&!inspectionPlayback);
    $('orbitMotion').checked=resume;inspectionPlayback=resume&&Boolean(selected);orbitFrame=null;
    resetPointerInput();closeDisplayPanel();syncOrbitControls();wake();
  }
  function advanceOrbits(time){
    const running=orbitRunning();
    if(running&&orbitFrame!==null)orbitSeconds+=Math.max(0,Math.min(.1,(time-orbitFrame)/1000))*Math.max(.1,Math.min(2,Number($('orbitSpeed').value)/100||1));
    orbitFrame=running?time:null;
    scene=galaxy?.sample?galaxy.sample(baseScene,orbitSeconds):baseScene;
    if(running&&inspectionPlayback&&selected)keepSelectedVisible();
    const status=!is3d()?'':running?selected?'边查看边运转':'轨道运转中':!$('orbitMotion').checked||reduceMotion.matches?'轨道已暂停':selected&&!inspectionPlayback?'查看暂停':hovered||drag||pinch||cameraFlight?'操作暂停':'轨道已暂停';
    if($('orbitStatus').textContent!==status)text('orbitStatus',status);
    canvas.dataset.orbitTime=orbitSeconds.toFixed(3);
  }
  const dimensionCameras=new Map();
  const is3d=()=>dimension==='3d';
  const camera=()=>({yaw,pitch,scale,panX,panY,width:viewWidth,height:viewHeight,distance:1800});
  const cameraScope=()=>JSON.stringify([viewMode,local?localCenter:null,dimension]);
  const cameraViews={oblique:{yaw:-.45,pitch:.55},top:{yaw:0,pitch:0},side:{yaw:0,pitch:1.2}};
  function applyCamera(value){({scale,panX,panY,yaw,pitch}=value);text('zoomLevel',Math.round(scale*100)+'%');}
  function stopCameraFlight(complete=false){const flight=cameraFlight;cameraFlight=null;if(complete&&flight){applyCamera(flight.to);syncSpatialUI();}}
  function moveCamera(action){
    stopCameraFlight();const from={scale,panX,panY,yaw,pitch};action();
    const to={scale,panX,panY,yaw,pitch};
    if(!is3d()||!$('cameraMotion').checked||reduceMotion.matches||suspended()||Object.keys(from).every(key=>from[key]===to[key]))return;
    cameraFlight={from,to,start:null};applyCamera(from);syncSpatialUI();wake();
  }
  function advanceCameraFlight(time){
    const flight=cameraFlight;if(!flight)return;
    flight.start??=time;const t=Math.max(0,Math.min(1,(time-flight.start)/420)),ease=t*t*(3-2*t);
    const value={};for(const key of ['scale','panX','panY','pitch'])value[key]=flight.from[key]+(flight.to[key]-flight.from[key])*ease;
    const turn=Math.atan2(Math.sin(flight.to.yaw-flight.from.yaw),Math.cos(flight.to.yaw-flight.from.yaw));value.yaw=flight.from.yaw+turn*ease;
    applyCamera(t===1?flight.to:value);if(t===1)cameraFlight=null;syncSpatialUI();
  }
  function rememberCamera(){stopCameraFlight();dimensionCameras.set(cameraScope(),{scale,panX,panY,yaw,pitch,galaxyTarget});}
  function systemNodes(id){const system=scene.systems.find(item=>item.id===id);return system?view.nodes.filter(node=>node.id===system.primary||scene.positions.get(node.id)?.parent===system.primary):[];}
  function syncGalaxySystems(){
    const systems=local?[]:scene.systems;
    if(!local&&!systems.some(system=>system.id===galaxyTarget))galaxyTarget='';
    const entries=systems.map(system=>({id:system.id,label:`${system.id} · ${system.label} · ${systemNodes(system.id).length}`}));
    const signature=JSON.stringify(entries);
    if(signature!==galaxyMenuSignature){
      const options=document.createDocumentFragment(),all=document.createElement('option');all.value='';all.textContent='全部星系';options.append(all);
      for(const entry of entries){const option=document.createElement('option');option.value=entry.id;option.textContent=entry.label;options.append(option);}
      $('galaxySystem').replaceChildren(options);galaxyMenuSignature=signature;
    }
    $('galaxySystem').value=local?'':galaxyTarget;
    $('galaxySystem').disabled=!is3d()||local||!systems.length;
    $('galaxySystemControl').hidden=!is3d()||local;
  }
  function locateGalaxySystem(){
    if(!is3d()||local||suspended())return;
    const id=$('galaxySystem').value;if(id&&!scene.systems.some(system=>system.id===id))return;
    resetPointerInput();closeDisplayPanel();galaxyTarget=id;syncSpatialUI();moveCamera(fit);
  }
  function syncSpatialUI(){
    syncGalaxySystems();
    syncOrbitControls();
    $('graphDimension').value=dimension;workspace.classList.toggle('galaxy-view',is3d());
    workspace.classList.toggle('particle-view',is3d()&&$('particleBodies').checked);
    $('particleBodies').disabled=!is3d();$('orbitMotion').disabled=!is3d()||reduceMotion.matches;$('orbitSpeed').disabled=!is3d()||reduceMotion.matches;$('orbitStatus').hidden=!is3d();
    $('graphNavigation').disabled=!is3d();$('resetCamera').disabled=!is3d();$('showOrbits').disabled=!is3d();
    $('cameraView').disabled=!is3d();$('focusNode').hidden=!is3d();$('focusNode').disabled=!is3d()||!selected;
    $('cameraView').value=Object.entries(cameraViews).find(([,v])=>Math.abs(Math.atan2(Math.sin(yaw-v.yaw),Math.cos(yaw-v.yaw)))<1e-6&&Math.abs(pitch-v.pitch)<1e-6)?.[0]||'custom';
    $('galaxyAngle').hidden=!is3d();$('galaxyGuide').hidden=!is3d();
    text('galaxyAngle',`方位 ${Math.round(yaw*180/Math.PI)}° · 仰角 ${Math.round(pitch*180/Math.PI)}°`);
    text('galaxyGuide',local?'中心恒星 · 一跳行星 · 更远卫星；轨道表示展示层级。':'主星为各记忆层连接较多的节点；轨道表示布局，关系线来自记录。');
    canvas.setAttribute('aria-label',is3d()?'三维记忆星系。拖动旋转，Shift 拖动平移，滚轮或双指缩放；点击星体或关系线查看详情。方向键旋转，Home 复位，也可用节点列表选择。':'平面记忆图谱。点击节点或连线查看详情，拖动平移，滚轮或双指缩放；也可用节点列表选择。');
    text('stageEyebrow',viewMode==='goals'?'GOALS & EVIDENCE':is3d()?'MEMORY CONSTELLATIONS':'THE SHAPE OF MEMORY');
    text('stageTitle',viewMode==='goals'?'目标进展，有据可查。':is3d()?'记忆，自成星系。':'每段记忆，都有来处。');
    const target=!local&&is3d()&&scene.systems.find(system=>system.id===galaxyTarget);
    text('immersiveScope',local?`局部关系 · ${view.nodes.length} 节点`:target?`${target.label} · ${systemNodes(target.id).length} 个已加载节点`:`全局图谱 · ${view.nodes.length} 节点`);
    syncImmersiveControls();
  }
  function syncImmersiveControls(){
    $('immersiveDetails').disabled=!selected;
    $('immersiveDetails').setAttribute('aria-expanded',String(Boolean(selected&&!detailsCollapsed)));
    text('immersiveDetails',selected&&!detailsCollapsed?'收起详情':'节点详情');
    $('closeImmersiveDetail').hidden=!immersive;
    $('immersiveRelations').hidden=!local;
    if(!local)workspace.classList.remove('immersive-local-open');
    $('immersiveRelations').setAttribute('aria-expanded',String(local&&workspace.classList.contains('immersive-local-open')));
  }
  function syncImmersiveUI(){workspace.classList.toggle('immersive-map',immersive);$('immersiveToggle').setAttribute('aria-pressed',String(immersive));$('immersiveToggle').title=immersive?'退出沉浸式星图':'进入沉浸式星图';$('immersiveHud').hidden=!immersive;if(immersive){workspace.classList.remove('mobile-explorer','explorer-hidden');}}
  function setImmersive(value){immersive=Boolean(value);syncImmersiveUI();syncImmersiveControls();syncPalette();resize();wake();}
  function toggleImmersiveDetail(){if(!selected)return;detailsCollapsed=!detailsCollapsed;if(!detailsCollapsed&&root.innerWidth<=700)workspace.classList.remove('immersive-local-open');workspace.classList.toggle('has-selection',!detailsCollapsed);syncImmersiveControls();syncVisibleArea();wake();}
  function resetCamera(){resetPointerInput();galaxyTarget='';moveCamera(()=>{yaw=-.45;pitch=.55;syncSpatialUI();fit();});}
  function changeCameraView(){
    const angles=cameraViews[$('cameraView').value];if(!is3d()||!angles||suspended())return;
    resetPointerInput();closeDisplayPanel();moveCamera(()=>{({yaw,pitch}=angles);syncSpatialUI();fit();});
  }
  function focusSelectedNode(){
    const node=model.map.get(selected);if(!is3d()||!node||suspended())return;
    galaxyTarget='';syncSpatialUI();
    resetPointerInput();closeDisplayPanel();moveCamera(()=>{scale=Math.min(4,Math.max(scale,1.5));
      const p=screen(node);panX+=viewWidth/2-p.x;panY+=viewHeight/2-p.y;keepSelectedVisible();
      text('zoomLevel',Math.round(scale*100)+'%');wake();});
  }
  function closeDisplayPanel(){$('displayPanel').hidden=true;$('displayToggle').setAttribute('aria-expanded','false');}
  function changeDimension(){
    rememberCamera();resetPointerInput();
    dimension=$('graphDimension').value==='3d'&&galaxy&&galaxyRenderer?'3d':'2d';
    const prior=dimensionCameras.get(cameraScope());syncSpatialUI();
    if(prior){({scale,panX,panY,yaw,pitch,galaxyTarget}=prior);text('zoomLevel',Math.round(scale*100)+'%');syncSpatialUI();if(prior.galaxyTarget&&!galaxyTarget&&!local&&is3d())fit();else{keepSelectedVisible();wake();}}
    else fit();
  }
  const pointers=new Map();let pinch=null;
  let requestVersion=0,loadController,detailVersion=0,detailController,searchTimer;
  let goalHistoryVersion=0,goalHistoryController=null;
  let goalActionVersion=0,goalActionController=null;
  let actionInputVersion=0,actionInputController=null,actionInputLoaded=false;
  const pendingToolChecks=new Set();
  let neighborVersion=0,neighborController=null,neighborBusy=false;
  const neighborPages=new Map();
  let inspectedEdge=null,relationVersion=0,relationController=null;
  let pathEdges=new Set();
  let relationMenuSignature='';
  const enabled=new Set(Object.keys(TIERS));
  let viewMode='memory',goalOffset=0,immersive=false;
  const tierRows=new Map();
  let paint={};
  function syncPalette(){
    if(!root.getComputedStyle)return;
    const css=root.getComputedStyle(immersive?workspace:document.documentElement),read=name=>css.getPropertyValue('--ui-'+name).trim();
    paint={edge:read('graph-edge'),origin:read('graph-origin'),path:read('graph-path'),highlight:read('graph-highlight'),text:read('text'),muted:read('muted')};
    for(const tier of Object.keys(TIERS)){const source=({G:'s1',R:'s3',V:'s2',E:'s4',A:'s5',I:'s2'})[tier]||tier.toLowerCase();TIERS[tier][1]=read('tier-'+source);tierRows.get(tier)?.style.setProperty('--tier-color',TIERS[tier][1]);}
    for(const node of model.nodes)node.color=TIERS[node.tier][1];
    if(selected&&model.map.has(selected))$('detailTier').style.setProperty('--tier-color',TIERS[model.map.get(selected).tier][1]);
    for(const item of $('nodeList').querySelectorAll('[data-node-id]')){const node=model.map.get(item.dataset.nodeId);if(node)item.querySelector('.tier-dot')?.style.setProperty('--tier-color',node.color);}
    wake();
  }
  root.addEventListener?.('theme-changed',syncPalette);
  const reduceMotion=root.matchMedia('(prefers-reduced-motion: reduce)');
  $('animateGraph').checked=!reduceMotion.matches;
  $('cameraMotion').checked=$('cameraMotion').checked&&!reduceMotion.matches;$('cameraMotion').disabled=reduceMotion.matches;
  $('orbitMotion').checked=$('orbitMotion').checked&&!reduceMotion.matches;
  reduceMotion.addEventListener('change',()=>{$('animateGraph').checked=!reduceMotion.matches;$('cameraMotion').disabled=reduceMotion.matches;if(reduceMotion.matches){$('cameraMotion').checked=false;$('orbitMotion').checked=false;stopCameraFlight(true);}orbitFrame=null;syncSpatialUI();wake();});
  function options(){return {tiers:enabled,origins:$('showOrigins').checked,orphans:$('showOrphans').checked,local,selected,center:localCenter,
    depth:Number($('localDepth').value)||1,direction:$('localDirection').value||'both',relationKind:$('localKind').value||'all',relationName:$('localRelation').value};}
  function message(text){$('graphMessage').textContent=text;$('graphMessage').hidden=!text;}
  function text(id,value){$(id).textContent=value;}
  function refreshView(){
    stopCameraFlight();
    resetPointerInput();
    view=visibleGraph(model,options());
    if(local&&!view.nodes.some(n=>n.id===localCenter)){
      cancelNeighborRead();
      local=false;localCenter=null;localHistory=[];globalCamera=null;detailsCollapsed=false;
      workspace.classList.toggle('has-selection',Boolean(selected));view=visibleGraph(model,options());
    }
    if(selected&&!view.nodes.some(n=>n.id===selected)){
      if(local){selected=localCenter;inspectionPlayback=false;const node=model.map.get(selected);renderDetail(node);readDetail(node);}
      else{clearSelection(false);view=visibleGraph(model,options());}
    }
    text('graphStats',`${view.nodes.length} 节点 · ${view.edges.length} 连接`);
    text('listCount',view.nodes.length);
    const fragment=document.createDocumentFragment();
    for(const n of view.nodes){const button=document.createElement('button');button.className='node-item'+(n.id===selected?' selected':'');button.title=n.label;button.dataset.nodeId=n.id;
      const dot=document.createElement('span');dot.className='tier-dot';dot.style.setProperty('--tier-color',n.color);
      const label=document.createElement('span');label.textContent=(local?(n.id===localCenter?'◎ 中心 · ':`${view.distances.get(n.id)} 层 · `):'')+n.label+(n.status?' · '+statusLabel(n.status):'');button.append(dot,label);button.addEventListener('click',()=>select(n.id,true));fragment.append(button);}
    $('nodeList').replaceChildren(fragment);
    for(const tier of Object.keys(TIERS)){
      const count=data?.counts[tier];text('count'+tier,count?.available?`${view.nodes.filter(n=>n.tier===tier).length} / ${count.total.toLocaleString()}`:'未连接');
    }
    message(view.nodes.length?'':data?'此范围内没有记录。\n可以更换关键词或调整筛选条件。':'正在读取 EVA 的记录…');
    $('globalView').classList.toggle('chosen',!local);$('localView').classList.toggle('chosen',local);
    $('globalView').setAttribute('aria-pressed',String(!local));$('localView').setAttribute('aria-pressed',String(local));
    const priorTarget=galaxyTarget;
    if(galaxy){baseScene=galaxy.layout(view,{local,center:localCenter});scene=galaxy.sample(baseScene,orbitSeconds);orbitFrame=null;}
    syncSpatialUI();renderLocalControls();renderPath();if(is3d())keepSelectedVisible();
    if(priorTarget&&!galaxyTarget&&!local&&is3d())fit();
    if($('relationInspector').open&&inspectedEdge){
      const fresh=view.edges.find(e=>edgeIdentity(e)===edgeIdentity(inspectedEdge));
      if(!fresh){$('relationInspector').close();message('已选关系不在当前投影或筛选范围内；不代表原始关系已删除。');}
      else if(fresh!==inspectedEdge)renderRelation(fresh);
    }
    if(selected)renderNeighbors();wake();
  }
  function renderLocalControls(){
    $('localControls').hidden=!local;
    $('stageCaption').hidden=local;
    if(!local){$('localBreadcrumbs').replaceChildren();workspace.style.setProperty('--local-inspector-top','51px');syncVisibleArea();return;}
    const center=model.map.get(localCenter);
    renderLocalBreadcrumbs();
    renderRelationFilter();
    const direction=({both:'双向',outgoing:'沿出向',incoming:'沿入向'})[$('localDirection').value||'both'];
    const kind=({all:'全部连接',stored_relation:'已存储关系',provenance:'事件来源',goal_evidence:'目标证据引用',action_input:'历史输入引用'})[$('localKind').value||'all'];
    const filterSummary=`${Number($('localDepth').value)||1} 层 · ${direction} · ${kind} · ${$('localRelation').value?relationLabel($('localRelation').value):'全部名称'}`;
    text('localFilterSummary',filterSummary);$('localFilterSummary').title=filterSummary;
    text('localCenterLabel',center?.label||'');$('localCenterLabel').title=center?.label||'';
    $('localBack').disabled=!localHistory.some(id=>model.map.has(id)&&enabled.has(model.map.get(id).tier));
    $('recenterLocal').disabled=!selected||selected===localCenter;
    text('toggleLocalDetail',detailsCollapsed?'节点详情':'收起详情');$('toggleLocalDetail').setAttribute('aria-expanded',String(!detailsCollapsed));
    $('expandLocal').disabled=Number($('localDepth').value)>=3;
    const one=[...view.distances.values()].filter(d=>d===1).length,more=[...view.distances.values()].filter(d=>d>1).length;
    text('localSummary',`${one} 个直接邻居${more?' · '+more+' 个间接邻居':''} · ${view.edges.length} 条连接`);
    text('localScope',view.nodes.length===1?'此范围内未显示邻居；可调整方向、类别、关系名称或记忆层筛选。':'点击邻居查看详情；用“以所选为中心”继续探索。');
    text('localProjectionNotice',`当前已加载 ${model.nodes.length} 个节点；“展开一层”仅遍历已加载图。“加载库中直接邻居”不受搜索词限制，显示仍遵循筛选。`);
    text('localLoadedScope',`仅探索已加载的 ${model.nodes.length} 个节点；可另行加载中心的直接邻居。`);
    const page=neighborPages.get(localCenter);
    $('loadNeighbors').hidden=viewMode==='goals';
    $('loadNeighbors').disabled=neighborBusy||page?.next===null;
    text('loadNeighbors',neighborBusy?'正在读取邻居…':page?.next===null?(page.incomplete?'本轮读取未完整':'本轮邻居已读完'):page?'继续加载邻居':'加载库中直接邻居');
    text('neighborReadStatus',neighborBusy?'正在读取中心节点的直接连接…':page?.notice||'每页最多 50 条连接；分页读取期间数据可能变化。');
    if(viewMode==='goals'){
      text('localProjectionNotice','探索当前页目标、回执、最近 5 次检查及显式加载的行动输入引用；连线不代表因果关系。');
      text('localLoadedScope','仅探索当前页已加载的记录和输入引用。');
      text('neighborReadStatus','更早目标可通过左侧翻页读取；完整检查总数见目标详情。');
    }
    positionLocalInspector();
  }
  function renderLocalBreadcrumbs(){
    const container=$('localBreadcrumbs'),ids=[...localHistory,localCenter].filter((id,index,array)=>id&&model.map.has(id)&&enabled.has(model.map.get(id).tier)&&(!index||id!==array[index-1]));
    const fragment=document.createDocumentFragment();
    ids.forEach((id,index)=>{
      if(index){const separator=document.createElement('span');separator.className='breadcrumb-separator';separator.textContent='›';separator.setAttribute('aria-hidden','true');fragment.append(separator);}
      const node=model.map.get(id),current=index===ids.length-1;
      if(current){const label=document.createElement('span');label.textContent=node.label;label.title=node.label;label.setAttribute('aria-current','page');fragment.append(label);return;}
      const button=document.createElement('button');button.type='button';button.textContent=node.label;button.title=`回到 ${node.label}`;button.addEventListener('click',()=>{localHistory=ids.slice(0,index);centerLocal(id,false);});fragment.append(button);
    });
    container.replaceChildren(fragment);
  }
  function renderRelationFilter(){
    const current=$('localRelation').value||'',base=visibleGraph(model,{...options(),relationName:''});
    const choices=relationChoices(base.edges,current),signature=JSON.stringify([current,choices]);
    if(signature!==relationMenuSignature){
      const fragment=document.createDocumentFragment(),all=document.createElement('option');all.value='';all.textContent=`全部名称（${base.edges.length}）`;fragment.append(all);
      for(const choice of choices){const option=document.createElement('option');option.value=choice.key;option.textContent=`${choice.label}（${choice.count}）`;fragment.append(option);}
      $('localRelation').replaceChildren(fragment);$('localRelation').value=current;relationMenuSignature=signature;
    }
    $('resetLocalFilters').disabled=!current&&($('localDirection').value||'both')==='both'&&($('localKind').value||'all')==='all';
    text('relationFilterNotice',current?`仅经过「${relationLabel(current)}」：路径中的每一步都须匹配。选项计数为应用名称筛选前的局部连接数；0 不代表记忆库中不存在。`:'名称选项来自当前已加载的局部范围，计数遵循层数、方向、类别及记忆层筛选。');
  }
  function positionLocalInspector(){if(local)workspace.style.setProperty('--local-inspector-top',($('localControls').getBoundingClientRect().bottom+8)+'px');syncVisibleArea();}
  function enterLocal(){
    if(!selected)return;
    rememberCamera();
    if(!local){globalCamera={scale,panX,panY,yaw,pitch,dimension,galaxyTarget};localHistory=[];localCenter=selected;}
    local=true;detailsCollapsed=root.innerWidth<=1150;workspace.classList.toggle('has-selection',!detailsCollapsed);refreshView();fit();
  }
  function centerLocal(id,remember=true){
    const node=model.map.get(id);if(!node||!enabled.has(node.tier))return;
    rememberCamera();
    if(remember&&localCenter&&localCenter!==id)localHistory=[...localHistory,localCenter].slice(-30);
    cancelNeighborRead();
    localCenter=id;selected=id;inspectionPlayback=false;detailsCollapsed=root.innerWidth<=1150;renderDetail(node);refreshView();fit();readDetail(node);
  }
  function exitLocal(){
    cancelNeighborRead();
    rememberCamera();
    const camera=globalCamera;local=false;localCenter=null;localHistory=[];globalCamera=null;detailsCollapsed=false;workspace.classList.toggle('has-selection',Boolean(selected));refreshView();
    const prior=camera&&camera.dimension===dimension?camera:dimensionCameras.get(cameraScope());
    if(prior){({scale,panX,panY,yaw,pitch,galaxyTarget}=prior);text('zoomLevel',Math.round(scale*100)+'%');syncSpatialUI();if(prior.galaxyTarget&&!galaxyTarget&&!local&&is3d())fit();else{keepSelectedVisible();wake();}}else fit();
  }
  for(const [tier,[label,color]] of Object.entries(TIERS)){
    const row=document.createElement('label');row.className='tier-row';row.style.setProperty('--tier-color',color);
    tierRows.set(tier,row);row.hidden=GOAL_TIERS.has(tier);
    const input=document.createElement('input');input.type='checkbox';input.checked=true;input.setAttribute('aria-label',tier+' '+label);
    input.addEventListener('change',()=>{input.checked?enabled.add(tier):enabled.delete(tier);refreshView();});
    const dot=document.createElement('span');dot.className='tier-dot';const name=document.createElement('span');name.className='tier-label';name.textContent=tier+' '+label;
    const count=document.createElement('span');count.className='tier-count';count.id='count'+tier;count.textContent='—';row.append(input,dot,name,count);$('tierFilters').append(row);
  }
  async function fetchJSON(url,signal,options={}){return root.EvaHttp.json(url,{...options,signal,cache:'no-store'});}
  function cancelActionInputRead(){
    actionInputVersion++;if(actionInputController){actionInputController.abort();text('actionInputStatus','输入读取已取消；可重新读取。');}
    actionInputController=null;if($('loadActionInputs'))$('loadActionInputs').disabled=false;
  }
  function renderActionInputs(node){
    cancelActionInputRead();const panel=$('actionInputs');if(!panel)return;
    const r=node.record||{};panel.hidden=!['G','R'].includes(node.tier)||!r.task_id||!(r.source_event_id||r.event_id);
    $('loadActionInputs').disabled=false;text('actionInputStatus','只读历史输入；不会重新执行请求或读取当前记忆正文。');
  }
  async function loadActionInputs(){
    const node=model.map.get(selected);if(!node||viewMode!=='goals'||$('actionInputs')?.hidden||$('loadActionInputs').disabled)return;
    cancelActionInputRead();const version=actionInputVersion,controller=actionInputController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    $('loadActionInputs').disabled=true;text('actionInputStatus','正在读取执行时保存的输入引用…');
    try{
      const r=node.record,incoming=await fetchJSON('/api/action-context/'+encodeURIComponent(r.task_id)+'/graph?'+new URLSearchParams({event_id:r.source_event_id||r.event_id}),controller.signal);
      if(version!==actionInputVersion||selected!==node.id||viewMode!=='goals')return;
      const merged=mergeActionInputs(model,incoming,node);model=buildGraph(merged,model.map);actionInputLoaded=true;
      for(const tier of ['A','I'])data.counts[tier]={available:true,total:model.nodes.filter(n=>n.tier===tier).length};
      refreshView();renderNeighbors();wake();
      text('actionInputStatus',`已加载 ${incoming.nodes.length-1} 个历史输入引用；沿连接查看版本与哈希。正文未复制，当前内容未核验。`);
      text('scopeStatus',`只读已存快照 · ${model.nodes.length} 个已加载节点 · 含行动输入引用`);
    }catch(error){if(version===actionInputVersion&&selected===node.id)text('actionInputStatus',(error.name==='AbortError'?'输入引用读取超时':error.message)+'；保留已有图谱。未启用或未记录 ACT-04 时不会生成输入连接。');}
    finally{clearTimeout(timer);if(version===actionInputVersion){actionInputController=null;$('loadActionInputs').disabled=false;}}
  }
  if($('loadActionInputs'))$('loadActionInputs').onclick=loadActionInputs;
  function cancelGoalHistoryRead(){goalHistoryVersion++;goalHistoryController?.abort();goalHistoryController=null;}
  function cancelGoalAction(){goalActionVersion++;goalActionController?.abort();goalActionController=null;}
  function historyRecordSummary(record){
    const files=(record.files||[]).map(file=>`${file.path}: ${file.outcome||'unknown'}`).join('\n');
    return `检查时间：${record.observed_at||'未记录'}\n目标版本：${record.base_goal_version??'未记录'} → ${record.committed_goal_version??'未记录'}${files?`\n${files}`:''}`;
  }
  function renderGoalHistory(node){
    cancelGoalHistoryRead();$('goalHistoryList').replaceChildren();$('loadGoalHistory').hidden=true;
    $('loadGoalHistory').dataset.nextOffset='';$('loadGoalHistory').disabled=false;text('goalHistoryStatus','');
    if(node.tier!=='G')return;
    const total=Number(node.verification_count)||0;
    if(total<=5){text('goalHistoryStatus',total?`已显示全部 ${total} 次检查。`:'尚未有检查记录。');return;}
    $('loadGoalHistory').hidden=false;$('loadGoalHistory').dataset.nextOffset='5';
    text('goalHistoryStatus',`当前图谱显示最近 5 / ${total} 次检查；加载更早历史不会触发新检查。`);
  }
  async function loadGoalHistory(){
    const node=model.map.get(selected);if(!node||node.tier!=='G'||$('loadGoalHistory').disabled)return;
    const offset=Number($('loadGoalHistory').dataset.nextOffset)||5;
    const version=++goalHistoryVersion;goalHistoryController?.abort();
    const controller=goalHistoryController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    $('loadGoalHistory').disabled=true;text('goalHistoryStatus','正在读取较早检查记录…');
    try{
      const incoming=await fetchJSON('/api/goals/'+encodeURIComponent(node.record_id)+'/history?'+new URLSearchParams({limit:'20',offset:String(offset)}),controller.signal);
      if(version!==goalHistoryVersion||selected!==node.id)return;
      const fragment=document.createDocumentFragment();
      for(const record of incoming.records||[]){
        const item=document.createElement('article');item.className='goal-history-item';
        const title=document.createElement('strong');title.textContent=`检查 · ${statusLabel(record.outcome)} · ${record.run_id||'未记录 ID'}`;
        const body=document.createElement('pre');body.textContent=historyRecordSummary(record);item.append(title,body);fragment.append(item);
      }
      $('goalHistoryList').append(fragment);
      const next=incoming.next_offset;
      $('loadGoalHistory').dataset.nextOffset=next===null?'':String(next);
      $('loadGoalHistory').hidden=next===null;$('loadGoalHistory').disabled=next===null;
      text('goalHistoryStatus',`已追加 ${incoming.records?.length||0} 条较早检查记录${next===null?'，历史已读完。':'。仍可继续读取。'}`);
    }catch(error){
      if(version===goalHistoryVersion){$('loadGoalHistory').disabled=false;text('goalHistoryStatus',(error.name==='AbortError'?'历史读取超时':error.message)+'；保留已显示记录，可重试。');}
    }finally{clearTimeout(timer);if(version===goalHistoryVersion)goalHistoryController=null;}
  }
  function renderGoalActions(node){
    cancelGoalAction();$('verifyGoal').hidden=true;$('cancelGoal').hidden=true;$('verifyGoal').disabled=false;$('cancelGoal').disabled=false;text('goalActionStatus','');
    if(node.tier!=='G')return;
    const status=node.status||node.record?.status,hasSpec=Boolean(node.record?.verification);
    $('verifyGoal').hidden=!(hasSpec&&['pending_verification','interrupted'].includes(status));
    $('cancelGoal').hidden=!['active','pending_verification','interrupted'].includes(status);
  }
  function renderToolChecks(node){
    const panel=$('toolChecks');if(!panel)return;
    panel.replaceChildren();panel.hidden=true;
    if(node.tier!=='E')return;
    for(const receipt of node.record?.tool_receipts?.receipts||[]){
      const saved=receipt.reconciliation;
      if(saved?.status!=='available')continue;
      const row=document.createElement('article');row.className='goal-history-item';
      const label=document.createElement('strong');label.textContent=saved.intent?.path||receipt.tool_id;
      const status=document.createElement('p');status.className='detail-notice';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      const check=document.createElement('button');check.className='relation-open';check.textContent='追加独立文件核验';
      check.hidden=receipt.status!=='unknown'||saved.count>=32;check.disabled=pendingToolChecks.has(receipt.action_id);
      if(check.disabled)status.textContent='核验请求仍在处理中；完成后刷新图谱读取结果。';
      const history=document.createElement('button');history.className='relation-open';history.textContent='读取全部已存核验';history.hidden=!saved.count;
      const body=document.createElement('pre');
      async function perform(sample){
        if(selected!==node.id||pendingToolChecks.has(receipt.action_id)||(sample?(check.hidden||check.disabled):(history.hidden||history.disabled)))return;
        pendingToolChecks.add(receipt.action_id);check.disabled=true;history.disabled=true;
        status.textContent=sample?'正在采样固定文件；不重做工具动作…':'正在读取已存核验…';
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
        const endpoint='/api/tool-observations/'+encodeURIComponent(receipt.action_id);
        try{
          const response=await fetchJSON(sample?endpoint:endpoint+'?'+new URLSearchParams({event_id:node.record.event_id}),controller.signal,sample?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:node.record.event_id,expected_count:saved.count})}:{});
          if(selected!==node.id||!Array.from(panel.children).includes(row))return;
          if(sample){pendingToolChecks.delete(receipt.action_id);status.textContent='核验已保存，正在刷新已存证据。';await load(true);}
          else{body.textContent=toolObservationSummary(response);status.textContent=`读取 ${response.count} 次已存核验；没有进行新的文件检查。`;}
        }catch(error){
          if(selected===node.id)status.textContent=(error.name==='AbortError'?'核验请求超时':error.message)+'；请刷新图谱或读取已存核验后再操作。页面不推断结果。';
        }finally{clearTimeout(timer);pendingToolChecks.delete(receipt.action_id);history.disabled=false;if(!sample)check.disabled=false;}
      }
      check.addEventListener('click',()=>perform(true));history.addEventListener('click',()=>perform(false));
      row.append(label,check,history,status,body);panel.append(row);panel.hidden=false;
    }
  }
  async function performGoalAction(action){
    const node=model.map.get(selected);if(!node||node.tier!=='G')return;
    const button=action==='verify'?$('verifyGoal'):$('cancelGoal');if(button.hidden||button.disabled)return;
    const version=++goalActionVersion;goalActionController?.abort();
    const controller=goalActionController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    $('verifyGoal').disabled=true;$('cancelGoal').disabled=true;text('goalActionStatus',action==='verify'?'正在执行指定文件检查…':'正在取消目标跟踪…');
    try{
      const goalId=node.record?.goal_id||node.record_id;
      const updated=await fetchJSON('/api/goals/'+encodeURIComponent(goalId)+'/'+(action==='verify'?'verify':'cancel'),controller.signal,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:node.record.version})});
      if(version!==goalActionVersion||selected!==node.id)return;
      text('goalActionStatus',`操作完成：${statusLabel(updated.status)}。正在刷新证据图谱。`);
      await load(true);
    }catch(error){
      if(version===goalActionVersion){$('verifyGoal').disabled=false;$('cancelGoal').disabled=false;text('goalActionStatus',(error.name==='AbortError'?'操作超时':error.message)+'；状态未由页面推断，可重试或刷新。');}
    }finally{clearTimeout(timer);if(version===goalActionVersion)goalActionController=null;}
  }
  function cancelNeighborRead(){neighborVersion++;neighborController?.abort();neighborController=null;neighborBusy=false;}
  async function loadNeighbors(){
    if(viewMode==='goals')return;
    const node=model.map.get(localCenter);if(!local||!node||neighborBusy)return;
    const previous=neighborPages.get(node.id)||{next:0,pages:0};if(previous.next===null)return;
    const version=++neighborVersion,query=$('graphSearch').value.trim();
    const controller=neighborController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    neighborBusy=true;renderLocalControls();
    try{
      const incoming=await fetchJSON('/api/memory/graph/neighbors?'+new URLSearchParams({tier:node.tier,record_id:node.record_id,limit:'50',offset:String(previous.next)}),controller.signal);
      if(version!==neighborVersion||!local||localCenter!==node.id||query!==$('graphSearch').value.trim())return;
      if(incoming.center!==node.id)throw new Error('邻居响应与当前中心不一致');
      const merged=mergeNeighborhood(model,incoming),incomplete=Boolean(incoming.scope?.incomplete||incoming.warnings?.length||merged.omitted);
      model=buildGraph(merged,model.map);steps=0;
      let notice=`第 ${previous.pages+1} 页：${incoming.edges.length} 条连接，新增 ${merged.addedNodes} 个节点、${merged.addedEdges} 条连接。`;
      if(merged.omitted)notice+='已达画布容量或有不完整端点，部分连接未加入；可刷新图谱后缩小搜索范围重试。';
      else if(incomplete)notice+='本次读取不完整，部分来源不可用或达到扫描上限。';
      else if(incoming.next_offset!==null)notice+='还有连接可继续读取。';
      else notice+='本轮直接连接已读取完毕；记忆层、类别和关系名称筛选仍然有效。';
      if(incoming.scope?.missing_endpoints)notice+=`另有 ${incoming.scope.missing_endpoints} 条关系缺少端点，未绘制。`;
      neighborPages.set(node.id,{next:merged.omitted?null:incoming.next_offset,pages:previous.pages+1,incomplete,notice});
      refreshView();fit();
      text('scopeStatus',`${model.nodes.length} 个已加载节点 · 含库中补充连接 · ${data.scope.nodes_truncated?'非完整记忆库':'有界投影'}`);
    }catch(error){
      if(version===neighborVersion&&localCenter===node.id)neighborPages.set(node.id,{...previous,notice:(error.name==='AbortError'?'邻居读取超时':error.message)+'；保留已有图谱，点击按钮重试。'});
    }finally{clearTimeout(timer);if(version===neighborVersion){neighborBusy=false;neighborController=null;renderLocalControls();}}
  }
  async function load(preserveView=false){
    stopCameraFlight();
    cancelActionInputRead();
    cancelNeighborRead();renderLocalControls();
    const version=++requestVersion;loadController?.abort();loadController=new AbortController();
    const controller=loadController;
    const timer=setTimeout(()=>controller.abort(),15000);
    const query=$('graphSearch').value.trim();
    text('graphRefreshStatus','');
    message('正在读取 EVA 的记录…');$('reloadGraph').disabled=true;
    $('previousGoals').disabled=true;$('nextGoals').disabled=true;
    try{
      const url=viewMode==='goals'?'/api/goals/graph?'+new URLSearchParams({limit:'40',offset:String(goalOffset),q:query}):'/api/memory/graph?'+new URLSearchParams({limit:'420',q:query});
      const incoming=await fetchJSON(url,controller.signal);
      if(version!==requestVersion||query!==$('graphSearch').value.trim())return;
      const keepView=preserveView&&data&&data.query===query;
      const priorSelection=selected,priorCenter=localCenter,listScroll=$('nodeList').scrollTop;
      const hadSupplement=neighborPages.size>0,hadActionInputs=actionInputLoaded;actionInputLoaded=false;
      neighborPages.clear();data=incoming;model=buildGraph(data,model.map);
      if(keepView){steps=0;}
      else{dimensionCameras.clear();galaxyTarget='';clearSelection(false);local=false;if(!is3d())for(let i=0;i<70;i++)step(model);steps=is3d()||reduceMotion.matches?0:130;}
      refreshView();
      if(keepView){
        $('nodeList').scrollTop=listScroll;
        if(selected){const node=model.map.get(selected);renderDetail(node);readDetail(node);}
      }else{fit();}
      const total=Object.values(data.counts).reduce((sum,c)=>sum+c.total,0);
      text('sourceStatus',data.preview?`UI 示例 · ${total.toLocaleString()} 个演示节点`:`SQLite · ${total.toLocaleString()} 条可用记录`);
      const centerLost=keepView&&priorCenter&&!local;
      $('sourceStatus').title=centerLost?'原中心不在本次投影与筛选范围内，已返回全局；不代表原始记录已删除。':keepView&&priorSelection&&!selected?'原选节点不在本次投影中，已清除选择；不代表原始记录已删除。':'';
      text('graphRefreshStatus',centerLost?'原中心已离开当前投影，已返回全局。':keepView&&priorSelection&&!selected
        ? '原选节点已离开当前投影，已清除选择。'
        : keepView?'已刷新，保留当前视图。':'');
      if(keepView&&hadSupplement)$('graphRefreshStatus').textContent+=' 已重置补充读取，可重新加载库中邻居。';
      if(keepView&&hadActionInputs)$('graphRefreshStatus').textContent+=' 已重置输入引用，可从原请求重新读取。';
      const scope=data.scope;
      text('scopeStatus',`${data.nodes.length} / ${scope.matched_nodes.toLocaleString()} 节点${scope.nodes_truncated?' · 部分展示':''}${scope.edges_truncated?' · 连线已限量':''}${scope.dangling_world_edges?' · '+scope.dangling_world_edges+' 条关系缺少实体':''}`);
      $('scopeStatus').title=`读取于 ${new Date(data.generated_at).toLocaleString('zh-CN')}。S2 仅未过期，S3 仅活跃记录；S5 优先展示对话事件。缺少端点实体的关系不绘制，也不补造实体。来源标注不等同于事实证明。`;
      $('openVault').hidden=!data.obsidian?.index_available;
      if(data.obsidian?.index_available)$('openVault').href=data.obsidian.index_uri;
      if(data.warnings?.length){text('sourceStatus','部分记忆层不可用');$('sourceStatus').title=data.warnings.join('\n');}
      if(viewMode==='goals'){
        text('sourceStatus',`SQLite · ${scope.matched_goals} 个匹配目标`);
        text('scopeStatus',`只读已存快照 · 本页 ${data.nodes.filter(n=>n.tier==='G').length} 个目标${scope.history_truncated?' · 检查历史部分展示':''}${scope.episode_references_unavailable?' · '+scope.episode_references_unavailable+' 个执行引用不可用':''}`);
        $('scopeStatus').title=`读取于 ${data.generated_at}。请求已处理不等于目标完成。文件检查仅反映采样时刻的指定内容；此页面不对账、不触发检查、不推进过期状态。`;
      }
      return true;
    }catch(error){if(version===requestVersion){message(`${error.name==='AbortError'?'读取超时':error.message}${data?'。保留上次图谱。':''}\n${viewMode==='goals'?'目标服务可能未启用或暂不可用。':''}点击左下角刷新按钮重试。`);text('sourceStatus','连接失败 · 数据未更新');}}
    finally{clearTimeout(timer);if(version===requestVersion){$('reloadGraph').disabled=false;renderGoalPaging();}}
  }
  function renderGoalPaging(){
    if(viewMode!=='goals')return;
    const scope=data?.scope;
    $('previousGoals').disabled=!scope||scope.goal_offset===0;
    $('nextGoals').disabled=!data||data.next_offset===null;
    text('goalPageStatus',scope?`${Math.floor(scope.goal_offset/40)+1} / ${Math.max(1,Math.ceil(scope.matched_goals/40))} 页`:'尚未读取');
  }
  function changeSource(initialQuery=''){
    viewMode=$('graphSource').value==='goals'?'goals':'memory';goalOffset=0;
    clearTimeout(searchTimer);clearSelection(false);cancelRelationRead();$('relationInspector').close();
    data=null;model=buildGraph({});neighborPages.clear();
    $('graphSearch').value=typeof initialQuery==='string'?initialQuery:'';$('graphSearch').placeholder=viewMode==='goals'?'搜索目标描述或 ID…':'搜索全部记忆…';
    $('graphSearch').setAttribute('aria-label',$('graphSearch').placeholder);
    $('localKind').value='all';$('localRelation').value='';
    for(const [tier,row] of tierRows)row.hidden=GOAL_TIERS.has(tier)!==(viewMode==='goals');
    for(const id of ['goalPaging','goalScope'])$(id).hidden=viewMode!=='goals';
    if($('newGoal'))$('newGoal').hidden=viewMode!=='goals';
    for(const id of ['exportNotes','openDiagnostics','originKey'])$(id).hidden=viewMode==='goals';
    $('openVault').hidden=true;
    text('solidKey',viewMode==='goals'?'记录与输入引用':'已存储关系');
    text('tierHeading',viewMode==='goals'?'记录类别':'记忆层级');
    text('countHeading',viewMode==='goals'?'显示 / 本页':'显示 / 总数');
    text('stageEyebrow',viewMode==='goals'?'GOALS & EVIDENCE':'THE SHAPE OF MEMORY');
    text('stageTitle',viewMode==='goals'?'目标进展，有据可查。':'每段记忆，都有来处。');
    text('stageSubtitle',viewMode==='goals'?'区分请求处理、目标状态与指定文件检查':'探索 EVA 记忆之间已记录的连接');
    text('sourceStatus','正在读取…');text('scopeStatus','有界只读投影');
    refreshView();return load();
  }
  root.addEventListener('eva:goal-created',async event=>{
    const id=event.detail?.goal_id;
    if(typeof id!=='string'||!id||id.length>200||$('newGoal')?.disabled)return;
    $('graphSource').value='goals';
    const loaded=await changeSource(id);
    if(loaded&&viewMode==='goals'&&$('graphSearch').value===id&&model.map.has('G:'+id))select('G:'+id);
  });
  function renderDetail(n){
    renderActionInputs(n);
    cancelGoalHistoryRead();
    $('detailEmpty').hidden=true;$('detailContent').hidden=false;workspace.classList.toggle('has-selection',!detailsCollapsed);
    text('detailTier',n.tier+' · '+TIERS[n.tier][0]);$('detailTier').style.setProperty('--tier-color',TIERS[n.tier][1]);
    text('detailTitle',n.label);$('detailTitle').title=n.label;text('detailKind',n.kind);text('detailOrigin',ORIGINS[n.provenance?.epistemic_status]||ORIGINS.unknown);
    text('detailTime',n.timestamp?new Date(n.timestamp).toLocaleString('zh-CN',{hour12:false}):'未记录');text('detailSource',n.source||'未标注');
    text('detailBody',n.content||'（无正文）');text('detailProvenance',JSON.stringify({record_id:n.record_id,...n.provenance,field_provenance:n.field_provenance},null,2));
    text('detailNotice',n.content_truncated?'内容超过显示上限，当前为截断视图。':'');
    $('openNote').hidden=!n.obsidian_uri;if(n.obsidian_uri)$('openNote').href=n.obsidian_uri;
    const isGoal=GOAL_TIERS.has(n.tier);$('goalDetail').hidden=!isGoal;
    text('detailBodyHeading',isGoal?'已存字段记录':'记忆内容');
    if(isGoal){
      text('detailOrigin','已存业务记录（非事实认证）');
      text('goalStatus',statusLabel(n.status));text('goalSummary',evidenceSummary(n));
      text('detailNotice',n.notice||'');
    }
    renderGoalActions(n);
    renderToolChecks(n);
    renderGoalHistory(n);
    syncImmersiveControls();
    syncVisibleArea();
  }
  function renderNeighbors(){
    const edges=view.edges.filter(e=>e.source===selected||e.target===selected);text('neighborCount',edges.length);
    const fragment=document.createDocumentFragment();
    for(const edge of edges){const n=edge.source===selected?edge.b:edge.a,button=document.createElement('button');button.className='neighbor';
      const label=document.createElement('span');label.textContent=n.label;const meta=document.createElement('small');
      meta.textContent=`${edge.source===edge.target?'↻ 自身':edge.source===selected?'→ 指向':'← 来自'} · ${edge.relation==='source_event'?'来源事件':INPUT_RELATIONS[edge.relation]||edge.relation} · ${edge.kind==='action_input'?'历史输入引用':edge.kind==='provenance'?'事件来源':ORIGINS[edge.provenance?.epistemic_status]||'未知来源'}`;
      button.append(label,meta);button.addEventListener('click',()=>select(n.id,true));
      const row=document.createElement('div');row.className='neighbor-row';
      const inspect=document.createElement('button');inspect.className='relation-open';inspect.textContent='关系详情';inspect.setAttribute('aria-label',`关系详情：${edge.relation}，${edge.a.label} → ${edge.b.label}`);
      inspect.addEventListener('click',()=>openRelations([edge]));row.append(button,inspect);fragment.append(row);}
    if(!edges.length){const p=document.createElement('p');p.className='detail-notice';p.textContent='当前投影与筛选范围内没有连接；不代表记忆库中没有关系。';fragment.append(p);}
    $('neighbors').replaceChildren(fragment);
  }
  function renderPath(){
    pathEdges=new Set();$('localPath').hidden=!local||!selected;
    $('localPathSteps').replaceChildren();
    if(!local||!selected)return;
    const path=connectionPath(view,localCenter,selected);
    $('fitLocalPath').disabled=!path?.length;
    text('localPathSummary',path===null?'当前筛选范围内没有可显示的路径。':path.length?`${path.length} 步连接 · 当前已加载图中的一条最短路径`:'所选节点就是当前中心。');
    if(!path?.length)return;
    const fragment=document.createDocumentFragment();
    const nodeButton=id=>{const button=document.createElement('button');button.className='path-node';button.textContent=model.map.get(id)?.label||id;button.title=id;button.addEventListener('click',()=>select(id,true));return button;};
    fragment.append(nodeButton(localCenter));
    for(const item of path){
      pathEdges.add(edgeIdentity(item.edge));
      const row=document.createElement('div');row.className='path-step';
      const button=document.createElement('button');button.className='path-relation';
      const forward=item.edge.source===item.from,name=item.edge.relation==='source_event'?'来源事件':item.edge.relation||'未标注';
      button.textContent=`${forward?'↓':'↑'} ${name} · ${forward?'顺向':'逆向'}经过`;
      button.setAttribute('aria-label',`路径关系：${item.edge.a.label} → ${item.edge.b.label}，${name}`);
      button.addEventListener('click',()=>openRelations([item.edge]));
      row.append(button,nodeButton(item.to));fragment.append(row);
    }
    $('localPathSteps').replaceChildren(fragment);
  }
  function cancelRelationRead(){relationVersion++;relationController?.abort();relationController=null;}
  function openRelations(edges){
    cancelRelationRead();inspectedEdge=null;$('relationFields').hidden=true;$('relationSourceRecord').hidden=true;
    const fragment=document.createDocumentFragment();
    for(const edge of edges.slice(0,100)){
      const button=document.createElement('button');button.className='relation-choice';
      button.textContent=`${edge.a.label} → ${edge.b.label} · ${edge.relation==='source_event'?'来源事件':edge.relation}`;
      button.addEventListener('click',()=>renderRelation(edge));fragment.append(button);
    }
    $('relationChoices').replaceChildren(fragment);$('relationChoices').hidden=edges.length<=1;
    text('relationChoiceNotice',edges.length>1?`此位置有 ${edges.length} 条连接，请选择要查看的关系${edges.length>100?'（本次列出前 100 条，其余可从节点连接列表查看）':''}。`:'');
    if(!$('relationInspector').open)$('relationInspector').showModal();
    if(edges.length===1)renderRelation(edges[0]);syncAnimation();
  }
  function renderRelation(candidate){
    cancelRelationRead();
    const edge=view.edges.find(e=>edgeIdentity(e)===edgeIdentity(candidate));
    if(!edge){text('relationChoiceNotice','该关系已离开当前投影，请关闭后重新选择。');$('relationFields').hidden=true;return;}
    inspectedEdge=edge;$('relationFields').hidden=false;$('relationSourceRecord').hidden=true;
    text('relationName',edge.relation==='source_event'?'来源事件':edge.relation||'未标注');
    text('relationFrom',edge.a.label);text('relationTo',edge.b.label);
    text('relationFromId',edge.source);text('relationToId',edge.target);
    text('relationKind',edge.kind==='provenance'?'事件来源引用':'已存储有向关系');
    text('relationOrigin',ORIGINS[edge.provenance?.epistemic_status]||ORIGINS.unknown);
    text('relationSource',edge.provenance?.source||'未标注');
    text('relationTime',edge.timestamp?new Date(edge.timestamp).toLocaleString('zh-CN',{hour12:false}):'未记录');
    text('relationWeight',edge.kind==='provenance'?'不适用（来源引用）':Number.isFinite(edge.weight)?String(edge.weight):'未记录');
    text('relationExplanation',edge.kind==='provenance'?'该连接来自记录或当前字段明确引用的来源事件；引用本身不证明内容属实。':'方向、名称和权重来自已存储关系。权重不是可信概率，来源标注也不等同于事实证明。');
    if(edge.kind==='goal_evidence'){
      text('relationKind','目标证据引用');text('relationWeight','不适用（记录引用）');
      text('relationExplanation',edge.relation==='source_event_episode'
        ? '目标来源事件与已存执行记录的事件 ID 一致，且记录身份已核对。引用不等于目标成功；查看不会重放动作。'
        : '依据已存目标 ID 关联回执或检查记录。引用不等于目标成功；检查仅证明指定条件在采样时刻的结果。');
    }
    const {a,b,...record}=edge;text('relationRaw',JSON.stringify(record,null,2));
    if(edge.kind==='action_input'){
      text('relationName',INPUT_RELATIONS[edge.relation]||edge.relation);text('relationKind','历史输入引用');text('relationWeight','不适用（执行输入引用）');
      text('relationExplanation','来自已保存的 Agent 行动及输入引用，身份已核对。只记录执行时的版本与哈希；不提供历史正文、不核验当前记忆，也不证明因果或目标成功。');
    }
    const eventId=edge.kind==='provenance'?edge.b.record_id:edge.provenance?.source_event_id;
    $('readRelationSource').hidden=!eventId;$('readRelationSource').disabled=false;
    text('relationSourceStatus','');
    $('readRelationSource').onclick=()=>readRelationSource(eventId);
    $('relationFrom').onclick=()=>{const id=edge.source;$('relationInspector').close();select(id,true);};
    $('relationTo').onclick=()=>{const id=edge.target;$('relationInspector').close();select(id,true);};
    wake();
  }
  async function readRelationSource(eventId){
    cancelRelationRead();const version=relationVersion;
    const controller=relationController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    $('readRelationSource').disabled=true;$('relationSourceRecord').hidden=false;
    text('relationSourceStatus','正在读取来源事件…');text('relationSourceBody','');
    try{
      const node=await fetchJSON('/api/memory/graph/node?'+new URLSearchParams({tier:'S5',record_id:eventId}),controller.signal);
      if(version!==relationVersion||!$('relationInspector').open)return;
      text('relationSourceBody',node.content||'（无正文）');
      text('relationSourceStatus',`来源事件：${node.record_id}${node.content_truncated?' · 内容已截断':''}；该记录不代表关系已获证实。`);
    }catch(error){if(version===relationVersion&&$('relationInspector').open)text('relationSourceStatus',(error.name==='AbortError'?'来源事件读取超时':error.message)+'；未能验证来源内容，可重试。');}
    finally{clearTimeout(timer);if(version===relationVersion)$('readRelationSource').disabled=false;}
  }
  async function select(id,focus=false){
    const n=model.map.get(id);if(!n)return;selected=id;inspectionPlayback=false;orbitFrame=null;detailsCollapsed=false;
    if(is3d()&&!local&&galaxyTarget&&!systemNodes(galaxyTarget).some(node=>node.id===id))galaxyTarget='';
    if(immersive&&root.innerWidth<=700)workspace.classList.remove('immersive-local-open');
    $('localView').disabled=false;renderDetail(n);refreshView();renderNeighbors();workspace.classList.remove('mobile-explorer');
    if(focus){if(is3d()){const p=screen(n);panX+=viewWidth/2-p.x;panY+=viewHeight/2-p.y;}else{panX=-n.x*scale;panY=-n.y*scale;}}
    keepSelectedVisible();
    wake();
    await readDetail(n);
  }
  async function readDetail(n){
    const version=++detailVersion;detailController?.abort();
    if(GOAL_TIERS.has(n.tier))return;
    const controller=detailController=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{const full=await fetchJSON('/api/memory/graph/node?'+new URLSearchParams({tier:n.tier,record_id:n.record_id}),controller.signal);if(selected===n.id&&version===detailVersion)renderDetail(full);}
    catch(error){if(selected===n.id&&version===detailVersion)text('detailNotice',(error.name==='AbortError'?'详情读取超时':error.message)+'；当前显示图谱摘要。');}
    finally{clearTimeout(timer);}
  }
  function clearSelection(refresh=true){cancelActionInputRead();cancelNeighborRead();cancelGoalHistoryRead();cancelGoalAction();selected=null;inspectionPlayback=false;detailVersion++;detailController?.abort();local=false;localCenter=null;localHistory=[];globalCamera=null;detailsCollapsed=false;$('localView').disabled=true;workspace.classList.remove('has-selection');$('detailContent').hidden=true;$('detailEmpty').hidden=false;if(refresh)refreshView();}
  // Overlay drawers reduce the usable camera area without changing the user's zoom.
  function syncVisibleArea(){
    const localOverlay=immersive&&local&&root.innerWidth<=700&&workspace.classList.contains('immersive-local-open');
    if(localOverlay){detailsCollapsed=true;workspace.classList.remove('has-selection');syncImmersiveControls();}
    const rect=canvas.getBoundingClientRect(),panel=$(localOverlay?'localControls':'inspector').getBoundingClientRect();
    let nextWidth=width,nextHeight=height;
    const overlaps=(workspace.classList.contains('has-selection')||localOverlay)&&panel.width>0&&panel.height>0&&
      panel.left<rect.left+width&&panel.left+panel.width>rect.left&&panel.top<rect.top+height&&panel.top+panel.height>rect.top;
    if(overlaps&&root.innerWidth<=700)nextHeight=Math.max(0,Math.min(height,panel.top-rect.top));
    else if(overlaps&&(root.innerWidth<=1150||immersive))nextWidth=Math.max(0,Math.min(width,panel.left-rect.left));
    const changed=nextWidth!==viewWidth||nextHeight!==viewHeight;
    viewWidth=nextWidth;viewHeight=nextHeight;
    workspace.style.setProperty('--graph-view-right',(width-viewWidth)+'px');
    workspace.style.setProperty('--graph-view-bottom',(height-viewHeight)+'px');
    if(changed){resetPointerInput();keepSelectedVisible();wake();}
  }
  function keepSelectedVisible(){
    const node=model.map.get(selected);if(!node||!viewWidth||!viewHeight)return;
    if(is3d()&&!local&&galaxyTarget&&!systemNodes(galaxyTarget).some(item=>item.id===selected))return;
    const p=screen(node),insetX=Math.min(24,viewWidth/2),insetY=Math.min(24,viewHeight/2),bottom=Math.max(insetY,viewHeight-(immersive?32:80));
    const top=immersive?Math.min(bottom,root.innerWidth<=700?172:184):insetY;
    panX+=Math.max(insetX,Math.min(viewWidth-insetX,p.x))-p.x;
    panY+=Math.max(top,Math.min(bottom,p.y))-p.y;
  }
  function screen(n){return is3d()?galaxy.project(scene.positions.get(n.id)||{x:0,y:0,z:0},camera()):{x:viewWidth/2+panX+n.x*scale,y:viewHeight/2+panY+n.y*scale};}
  function hit(x,y){let best=null,dist=Infinity,depth=-Infinity;for(const n of view.nodes){const p=screen(n);if(p.visible===false)continue;const d=Math.hypot(p.x-x,p.y-y),r=is3d()?galaxyRenderer.bodyRadius(n,scene.positions.get(n.id),p,scale)+4:Math.max(9,radius(n)*scale+5);
    if(d<r&&(is3d()?(p.depth>depth||(p.depth===depth&&d<dist)):d<dist)){best=n;dist=d;depth=p.depth;}}return best;}
  function radius(n){return 1.7+Math.min(5,Math.sqrt(n.degree)*.6);}
  function zoom(factor,x=viewWidth/2,y=viewHeight/2){stopCameraFlight();const old=scale;scale=Math.max(.15,Math.min(4,scale*factor));panX=x-viewWidth/2-(x-viewWidth/2-panX)*scale/old;panY=y-viewHeight/2-(y-viewHeight/2-panY)*scale/old;text('zoomLevel',Math.round(scale*100)+'%');wake();}
  function fit(){const observed=is3d()&&!local&&galaxyTarget?systemNodes(galaxyTarget):null;fitNodes(observed||view.nodes,Boolean(observed));}
  function fitNodes(nodes,observed=false){stopCameraFlight();if(!nodes.length)return;const points=is3d()?nodes.map(n=>galaxy.project(scene.positions.get(n.id)||{x:0,y:0,z:0},{...camera(),scale:1,panX:0,panY:0,width:0,height:0})).filter(p=>p.visible):nodes;
    if(!points.length)return;const xs=points.map(n=>n.x),ys=points.map(n=>n.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
    const top=immersive?Math.min(viewHeight,root.innerWidth<=700?172:184):0;
    const bottom=immersive?Math.min(viewHeight-top,viewHeight<height?32:root.innerWidth<=700?170:110):0;
    scale=Math.max(.15,Math.min(1.5,(viewWidth-100)/Math.max(180,maxX-minX),(viewHeight-top-bottom-(immersive||observed?70:local?100:195))/Math.max(180,maxY-minY)));
    panX=-(minX+maxX)/2*scale;panY=(immersive?(top-bottom)/2:local?0:25)-(minY+maxY)/2*scale;
    if(!observed||nodes.some(node=>node.id===selected))keepSelectedVisible();text('zoomLevel',Math.round(scale*100)+'%');wake();}
  function resize(){
    resetPointerInput();
    const previousWidth=width,previousHeight=height,rect=canvas.getBoundingClientRect();
    // Native toggle events and ResizeObserver can arrive in either order.
    // Read the disclosure state here so the first resize preserves its camera.
    const filtersChanged=filterPanelOpen!==$('localFilterPanel').open;
    filterPanelOpen=$('localFilterPanel').open;
    width=rect.width;height=rect.height;workspace.style.setProperty('--graph-stage-height',height+'px');
    const dpr=Math.min(2,root.devicePixelRatio||1);
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);
    positionLocalInspector();
    if(view.nodes.length&&(width!==previousWidth||(height!==previousHeight&&!filtersChanged)))fit();
    wake();
  }
  new ResizeObserver(resize).observe(canvas);
  new ResizeObserver(syncVisibleArea).observe($('inspector'));
  function suspended(){return document.hidden||$('relationDiagnostics')?.open||$('relationInspector')?.open||$('goalCreateDialog')?.open;}
  function wake(){if(!raf&&!suspended())raf=requestAnimationFrame(frame);}
  function syncAnimation(){orbitFrame=null;if(suspended()){resetPointerInput();if(raf)cancelAnimationFrame(raf);raf=0;}else{wake();}}
  function frame(time){raf=0;if(suspended())return;if(time-lastFrame<32){wake();return;}lastFrame=time;
    advanceOrbits(time);advanceCameraFlight(time);
    if(steps>0){if(!is3d())step(model,drag?.node?.id);steps--;}
    draw(time);if(orbitRunning()||cameraFlight||steps>0||drag||($('animateGraph').checked&&view.nodes.length))wake();
  }
  function labelObstacles(){
    if(!immersive)return [];
    const canvasRect=canvas.getBoundingClientRect(),boxes=[];
    for(const element of [$('globalView').closest?.('.graph-toolbar'),$('graphDimension').closest?.('.spatial-toolbar')]){
      if(!element)continue;const r=element.getBoundingClientRect();
      if(r.width&&r.height)boxes.push({left:r.left-canvasRect.left-4,right:r.left-canvasRect.left+r.width+4,top:r.top-canvasRect.top-4,bottom:r.top-canvasRect.top+r.height+4});
    }
    return boxes;
  }
  function draw(time){
    if(is3d()){const result=galaxyRenderer.draw(ctx,{scene,view,camera:camera(),clearWidth:width,clearHeight:height,paint,selected,hovered:hovered?.id,localCenter,observedSystem:local?'':galaxyTarget,showLabels:$('showLabels').checked,showOrbits:$('showOrbits').checked,particleBodies:$('particleBodies').checked,labelExclusions:labelObstacles(),edgeBrightness:Number($('edgeBrightness').value)/100,animate:$('animateGraph').checked,immersive,time,pathEdges});canvas.dataset.particleCount=String(result.particles||0);return;}
    ctx.clearRect(0,0,width,height);const active=selected||hovered?.id,near=new Set(active?[active]:[]);
    if(active)for(const e of view.edges)if(e.source===active||e.target===active){near.add(e.source);near.add(e.target);}
    const brightness=Number($('edgeBrightness').value)/100,animate=$('animateGraph').checked,edgeLabels=[];
    for(let i=0;i<view.edges.length;i++){
      const e=view.edges[i],a=screen(e.a),b=screen(e.b),connected=active&&(e.source===active||e.target===active);
      const onPath=pathEdges.has(edgeIdentity(e));
      ctx.globalAlpha=onPath?1:local?(connected?.8:.4):active?(connected?.66:.035):brightness*.5;ctx.strokeStyle=onPath?paint.path:e.kind==='provenance'?paint.origin:paint.edge;ctx.lineWidth=onPath?2.3:connected?1:.6;
      ctx.setLineDash(e.kind==='provenance'?[3,5]:[]);ctx.beginPath();
      if(e.source===e.target)ctx.arc(a.x+10,a.y-10,13,0,Math.PI*2);else{ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);}ctx.stroke();ctx.setLineDash([]);
      if(local){
        const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy);
        if(d>24){
          const ux=dx/d,uy=dy/d,x=b.x-ux*12,y=b.y-uy*12;
          ctx.beginPath();ctx.moveTo(x-ux*6-uy*3,y-uy*6+ux*3);ctx.lineTo(x,y);ctx.lineTo(x-ux*6+uy*3,y-uy*6-ux*3);ctx.stroke();
          if(i<24&&d>90){
            const name=e.relation==='source_event'?'来源事件':e.relation||'关系',label=name.length>14?name.slice(0,14)+'…':name;
            ctx.font='11px "Segoe UI","Microsoft YaHei",sans-serif';ctx.textAlign='center';ctx.textBaseline='bottom';ctx.fillStyle=paint.muted;
            const x=(a.x+b.x)/2,w=ctx.measureText(label).width;
            for(const offset of [-4,-18,10]){
              const y=(a.y+b.y)/2+offset,box={left:x-w/2-3,right:x+w/2+3,top:y-12,bottom:y+2};
              if(edgeLabels.some(b=>box.left<b.right&&box.right>b.left&&box.top<b.bottom&&box.bottom>b.top))continue;
              edgeLabels.push(box);ctx.fillText(label,x,y);break;
            }
          }
        }
      }
      if(animate && i%7===0 && (!active||connected)){
        const phase=(time/9000+(hash(e.id||e.source)%1000)/1000)%1;
        ctx.globalAlpha=connected?.9:.5;ctx.fillStyle=e.a.color;ctx.beginPath();ctx.arc(a.x+(b.x-a.x)*phase,a.y+(b.y-a.y)*phase,1.2,0,Math.PI*2);ctx.fill();
      }
    }
    const top=new Set([...view.nodes].sort((a,b)=>b.degree-a.degree).slice(0,24).map(n=>n.id));
    const labels=[];
    for(const n of view.nodes){
      const p=screen(n),r=Math.max(local?3:1.15,radius(n)*Math.sqrt(scale)),highlight=n.id===active;
      if(p.x< -40||p.y< -40||p.x>width+40||p.y>height+40)continue;
      ctx.globalAlpha=local?1:active&&!near.has(n.id)?.15:1;
      const halo=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,r*5);halo.addColorStop(0,n.color+'55');halo.addColorStop(.4,n.color+'17');halo.addColorStop(1,n.color+'00');ctx.fillStyle=halo;ctx.beginPath();ctx.arc(p.x,p.y,r*5,0,Math.PI*2);ctx.fill();
      ctx.fillStyle=highlight?paint.highlight:n.color;ctx.beginPath();ctx.arc(p.x,p.y,highlight?r+1.3:r,0,Math.PI*2);ctx.fill();
      if(highlight){ctx.strokeStyle=n.color;ctx.lineWidth=.8;ctx.globalAlpha=.6;ctx.beginPath();ctx.arc(p.x,p.y,r+7,0,Math.PI*2);ctx.stroke();}
      if(local&&n.id===localCenter){ctx.strokeStyle=paint.path;ctx.lineWidth=1.5;ctx.globalAlpha=.9;ctx.beginPath();ctx.arc(p.x,p.y,r+11,0,Math.PI*2);ctx.stroke();}
      if($('showLabels').checked&&(local||highlight||(!active&&top.has(n.id)&&n.degree>0)||(active&&near.has(n.id)&&scale>1))){
        labels.push({n,p,r,highlight});
      }
    }
    // Declutter labels independently of the data; no edges are moved or invented.
    const occupied=local?[...edgeLabels]:[{left:0,right:320,top:0,bottom:115}];
    labels.sort((a,b)=>Number(b.highlight)-Number(a.highlight)||b.n.degree-a.n.degree);
    for(const {n,p,r,highlight} of labels){
      ctx.font=`${highlight?11:10}px "Segoe UI","Microsoft YaHei",sans-serif`;
      const label=n.label.length>16?n.label.slice(0,16)+'…':n.label;
      const w=ctx.measureText(label).width,box={left:p.x-w/2-6,right:p.x+w/2+6,top:p.y+r+5,bottom:p.y+r+23};
      if(!highlight && occupied.some(b=>box.left<b.right&&box.right>b.left&&box.top<b.bottom&&box.bottom>b.top))continue;
      if(box.left<4||box.right>width-4||box.bottom>height-50)continue;
      occupied.push(box);ctx.globalAlpha=highlight?1:.8;ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=highlight?paint.text:paint.muted;ctx.fillText(label,p.x,p.y+r+8);
    }
    ctx.globalAlpha=1;
  }
  function pointerPosition(e){const r=canvas.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};}
  function pinchPosition(){const [a,b]=pointers.values();return {x:(a.x+b.x)/2,y:(a.y+b.y)/2,distance:Math.hypot(b.x-a.x,b.y-a.y)};}
  function releaseCapture(id){if(canvas.hasPointerCapture?.(id))canvas.releasePointerCapture(id);}
  function resetPointerInput(){stopCameraFlight();const ids=[...pointers.keys()];pointers.clear();pinch=null;drag=null;hovered=null;$('graphTooltip').hidden=true;for(const id of ids)releaseCapture(id);}
  canvas.addEventListener('pointerdown',e=>{
    if(e.button!==0||pointers.size>=2||suspended())return;
    stopCameraFlight();
    canvas.setPointerCapture(e.pointerId);const {x,y}=pointerPosition(e);pointers.set(e.pointerId,{x,y});
    if(pointers.size===2){drag=null;pinch=pinchPosition();hovered=null;}
    else{const node=hit(x,y);drag={pointerId:e.pointerId,node,edges:node?[]:hitEdges(view.edges,screen,x,y),navigation:e.shiftKey?'pan':$('graphNavigation').value||'rotate',startX:x,startY:y,lastX:x,lastY:y,moved:false};}
    $('graphTooltip').hidden=true;wake();
  });
  canvas.addEventListener('pointermove',e=>{
    if(pointers.size&&!pointers.has(e.pointerId))return;
    const {x,y}=pointerPosition(e);
    if(pointers.has(e.pointerId))pointers.set(e.pointerId,{x,y});
    if(pinch){const next=pinchPosition();zoom(pinch.distance>0&&next.distance>0?next.distance/pinch.distance:1,pinch.x,pinch.y);
      panX+=next.x-pinch.x;panY+=next.y-pinch.y;pinch=next;wake();return;}
    if(drag){const dx=x-drag.lastX,dy=y-drag.lastY;drag.moved||=Math.hypot(x-drag.startX,y-drag.startY)>4;
      if(is3d()&&drag.navigation!=='pan'){yaw=(yaw+dx*.008)%(Math.PI*2);pitch=Math.max(-1.35,Math.min(1.35,pitch+dy*.006));syncSpatialUI();keepSelectedVisible();}
      else if(!is3d()&&drag.node){drag.node.x+=dx/scale;drag.node.y+=dy/scale;steps=80;}else{panX+=dx;panY+=dy;}
      drag.lastX=x;drag.lastY=y;wake();return;}
    hovered=hit(x,y);const edges=hovered?[]:hitEdges(view.edges,screen,x,y);$('graphTooltip').hidden=!hovered&&!edges.length;
    if(hovered||edges.length){text('graphTooltip',hovered?hovered.label:edges.length>1?`${edges.length} 条重叠连接 · 点击选择关系`:`${edges[0].a.label} → ${edges[0].b.label} · ${edges[0].relation} · 点击查看关系`);$('graphTooltip').style.left=Math.max(4,Math.min(width-240,x+15))+'px';$('graphTooltip').style.top=Math.max(4,Math.min(height-65,y+15))+'px';}
    canvas.style.cursor=hovered||edges.length?'pointer':'grab';wake();
  });
  function finishPointer(e,activate){
    if(!pointers.has(e.pointerId))return;
    const released=drag,wasPinching=Boolean(pinch);pointers.delete(e.pointerId);pinch=null;drag=null;
    if(pointers.size){const [pointerId,position]=[...pointers][0];drag={pointerId,node:null,edges:[],navigation:'pan',startX:position.x,startY:position.y,lastX:position.x,lastY:position.y,moved:true};}
    releaseCapture(e.pointerId);
    if(activate&&!wasPinching&&released&&!released.moved){if(released.node)select(released.node.id);else if(released.edges.length)openRelations(released.edges);else clearSelection();}
    wake();
  }
  canvas.addEventListener('pointerup',e=>finishPointer(e,true));
  canvas.addEventListener('pointercancel',e=>finishPointer(e,false));
  canvas.addEventListener('lostpointercapture',e=>finishPointer(e,false));
  canvas.addEventListener('pointerleave',()=>{hovered=null;$('graphTooltip').hidden=true;wake();});
  canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.0015),e.clientX-r.left,e.clientY-r.top);},{passive:false});
  canvas.addEventListener('keydown',e=>{if(e.key==='Home'&&is3d()){e.preventDefault();resetCamera();return;}if(['+','=','-','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){stopCameraFlight();e.preventDefault();if(e.key==='+'||e.key==='=')zoom(1.2);else if(e.key==='-')zoom(1/1.2);else if(is3d()&&!e.shiftKey&&$('graphNavigation').value!=='pan'){yaw+=(e.key==='ArrowLeft'?-.12:e.key==='ArrowRight'?.12:0);pitch=Math.max(-1.35,Math.min(1.35,pitch+(e.key==='ArrowUp'?-.1:e.key==='ArrowDown'?.1:0)));syncSpatialUI();keepSelectedVisible();wake();}else{panX+=e.key==='ArrowLeft'?30:e.key==='ArrowRight'?-30:0;panY+=e.key==='ArrowUp'?30:e.key==='ArrowDown'?-30:0;wake();}}});
  document.addEventListener('visibilitychange',syncAnimation);
  if($('relationDiagnostics'))new MutationObserver(syncAnimation).observe($('relationDiagnostics'),{attributes:true,attributeFilter:['open']});
  new MutationObserver(syncAnimation).observe($('relationInspector'),{attributes:true,attributeFilter:['open']});
  if($('goalCreateDialog'))new MutationObserver(syncAnimation).observe($('goalCreateDialog'),{attributes:true,attributeFilter:['open']});
  $('closeRelationInspector').onclick=()=>$('relationInspector').close();
  $('relationInspector').addEventListener('close',()=>{if($('relationInspector').open)return;cancelRelationRead();inspectedEdge=null;syncAnimation();});
  $('zoomIn').onclick=()=>zoom(1.25);$('zoomOut').onclick=()=>zoom(.8);$('fitGraph').onclick=()=>moveCamera(fit);
  $('graphDimension').onchange=changeDimension;$('resetCamera').onclick=resetCamera;
  $('cameraView').onchange=changeCameraView;$('focusNode').onclick=focusSelectedNode;
  $('galaxySystem').onchange=locateGalaxySystem;
  $('graphNavigation').onchange=resetPointerInput;
  $('showOrbits').oninput=wake;
  $('particleBodies').oninput=()=>{syncSpatialUI();wake();};
  $('orbitPlayback').onclick=toggleOrbitPlayback;
  $('orbitMotion').oninput=()=>{inspectionPlayback=false;orbitFrame=null;syncOrbitControls();wake();};
  $('orbitSpeed').oninput=()=>{orbitFrame=null;syncOrbitControls();wake();};
  $('clearSelection').onclick=()=>clearSelection();$('globalView').onclick=exitLocal;$('localView').onclick=enterLocal;
  $('recenterLocal').onclick=()=>{if(local&&selected)centerLocal(selected);};
  $('loadNeighbors').onclick=loadNeighbors;
  $('loadGoalHistory').onclick=loadGoalHistory;
  $('verifyGoal').onclick=()=>performGoalAction('verify');$('cancelGoal').onclick=()=>performGoalAction('cancel');
  $('toggleLocalDetail').onclick=()=>{detailsCollapsed=!detailsCollapsed;workspace.classList.toggle('has-selection',!detailsCollapsed);renderLocalControls();};
  $('localFilterPanel').addEventListener('toggle',resize);
  $('fitLocalPath').onclick=()=>{const path=local&&connectionPath(view,localCenter,selected);if(path?.length)moveCamera(()=>fitNodes([model.map.get(localCenter),...path.map(item=>model.map.get(item.to))]));};
  $('localBack').onclick=()=>{while(localHistory.length){const id=localHistory.pop();if(model.map.has(id)&&enabled.has(model.map.get(id).tier)){centerLocal(id,false);break;}}};
  for(const id of ['localDepth','localDirection','localKind','localRelation'])$(id).onchange=()=>{refreshView();fit();};
  $('resetLocalFilters').onclick=()=>{$('localDirection').value='both';$('localKind').value='all';$('localRelation').value='';refreshView();fit();};
  $('expandLocal').onclick=()=>{$('localDepth').value=String(Math.min(3,(Number($('localDepth').value)||1)+1));refreshView();fit();};
  $('displayToggle').onclick=()=>{const hidden=!$('displayPanel').hidden;closeDisplayPanel();if(!hidden){$('displayPanel').hidden=false;$('displayToggle').setAttribute('aria-expanded','true');$('displayPanel').querySelector('input,select,button')?.focus();}};
  $('immersiveToggle').onclick=()=>setImmersive(!immersive);$('immersiveExit').onclick=()=>setImmersive(false);$('immersiveReset').onclick=resetCamera;
  $('immersiveDetails').onclick=toggleImmersiveDetail;$('closeImmersiveDetail').onclick=toggleImmersiveDetail;
  $('immersiveRelations').onclick=()=>{if(!local)return;workspace.classList.toggle('immersive-local-open');syncImmersiveControls();syncVisibleArea();wake();};
  for(const id of ['showOrphans','showOrigins'])$(id).onchange=refreshView;
  for(const id of ['animateGraph','showLabels','edgeBrightness'])$(id).oninput=wake;
  $('cameraMotion').oninput=()=>{if(!$('cameraMotion').checked)stopCameraFlight(true);wake();};
  $('explorerToggle').onclick=()=>{if(immersive){setImmersive(false);if(root.innerWidth<=700)workspace.classList.add('mobile-explorer');return;}if(root.innerWidth<=700)workspace.classList.toggle('mobile-explorer');else workspace.classList.toggle('explorer-hidden');};
  $('graphSearch').addEventListener('input',()=>{stopCameraFlight();goalOffset=0;cancelNeighborRead();renderLocalControls();clearTimeout(searchTimer);text('graphRefreshStatus','');searchTimer=setTimeout(load,350);});
  $('graphSource').value='memory';$('graphSource').onchange=changeSource;
  $('previousGoals').onclick=()=>{if(data?.scope.goal_offset>0){goalOffset=Math.max(0,data.scope.goal_offset-40);load();}};
  $('nextGoals').onclick=()=>{if(data?.next_offset!=null){goalOffset=data.next_offset;load();}};
  $('reloadGraph').onclick=()=>{clearTimeout(searchTimer);load(true);};
  document.addEventListener('keydown',e=>{
    if(e.isComposing||e.keyCode===229||$('relationDiagnostics')?.open||$('relationInspector')?.open||$('goalCreateDialog')?.open)return;
    if(e.key==='Escape'){
      if(!$('displayPanel').hidden){e.preventDefault();closeDisplayPanel();$('displayToggle').focus();return;}
      if($('cameraOptions')?.open){e.preventDefault();$('cameraOptions').open=false;$('cameraOptions').querySelector('summary')?.focus();return;}
      if(workspace.classList.contains('mobile-explorer')){e.preventDefault();workspace.classList.remove('mobile-explorer');$('explorerToggle')?.focus();return;}
      if(immersive){setImmersive(false);return;}
      clearSelection();return;
    }
    const active=document.activeElement,editing=['INPUT','TEXTAREA','SELECT'].includes(active?.tagName)||active?.isContentEditable;
    if((e.key==='/'&&!editing&&!e.ctrlKey&&!e.metaKey&&!e.altKey)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k')){e.preventDefault();workspace.classList.add('mobile-explorer');workspace.classList.remove('explorer-hidden');$('graphSearch').focus();}
  });
  $('exportNotes').onclick=async()=>{const button=$('exportNotes');button.disabled=true;try{const archive=await root.EvaHttp.archive('/api/obsidian/export?limit=420',{cache:'no-store'});const url=URL.createObjectURL(archive),a=document.createElement('a');a.href=url;a.download='EVA-Memory.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}catch(error){message(error.message);}finally{button.disabled=false;}};
  syncSpatialUI();syncPalette();resize();load();
})(globalThis);
