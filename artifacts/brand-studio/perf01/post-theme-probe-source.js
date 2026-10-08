// Development-only browser observer. Not loaded by product templates or portable kits.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.EvaCubePerformance=api.install(root);
})(globalThis,function(){
  'use strict';
  function histogram(step=.25,limit=2000){
    const bins=new Uint32Array(Math.ceil(limit/step)+1);
    let count=0,sum=0,min=Infinity,max=0,over34=0,over50=0;
    return {add(value){if(!Number.isFinite(value)||value<0)return;count++;sum+=value;min=Math.min(min,value);max=Math.max(max,value);
      bins[Math.min(bins.length-1,Math.floor(value/step))]++;if(value>34)over34++;if(value>50)over50++;},
      summary(){const percentile=p=>{if(!count)return null;const wanted=Math.ceil(count*p);let seen=0;
        for(let i=0;i<bins.length;i++){seen+=bins[i];if(seen>=wanted)return i===bins.length-1?{atLeastMs:limit}:{lowerMs:i*step,upperMs:(i+1)*step};}};
        return {count,meanMs:count?sum/count:null,minMs:count?min:null,maxMs:count?max:null,p50:percentile(.5),p95:percentile(.95),p99:percentile(.99),over34,over50,stepMs:step,overflowAtMs:limit};}};
  }
  function install(win){
    if(win.EvaCubePerformance?.installed)return win.EvaCubePerformance;
    const doc=win.document,urls=new Map(),exports=[],events=[],samples=[];
    const frames=histogram(),originalRun=win.EvaBrandStudio.Preview.prototype.run;
    const originalTurn=win.CubeRendering.CubeAnimator.prototype.turn;
    const originalEmit=win.CubeLogoSystem.CubeLogo.prototype._emit;
    const originalCreate=win.URL.createObjectURL,originalRevoke=win.URL.revokeObjectURL;
    const originalToBlob=win.HTMLCanvasElement.prototype.toBlob;
    let preview=null,observedLogo=null,activeTurns=0,maxActiveTurns=0,turns=0,turnErrors=0;
    let running=false,hasStarted=false,started=0,ended=0,durationMs=0,raf=0,timer=0,stopTimer=0,lastFrame=null;
    let sampleIntervalMs=2500,finalSnapshot=null;
    let longTaskCount=0,longTaskTotal=0,longTaskMax=0,longTasks=[],observer=null;
    const pendingExports=new Map();let maxUrls=0,disposed=false;
    const now=()=>win.performance.now(),elapsed=()=>running?now()-started:ended-started;
    win.EvaBrandStudio.Preview.prototype.run=function(...args){preview=this;return originalRun.apply(this,args);};
    win.CubeLogoSystem.CubeLogo.prototype._emit=function(...args){observedLogo=this;return originalEmit.apply(this,args);};
    win.CubeRendering.CubeAnimator.prototype.turn=async function(...args){
      if(running){turns++;activeTurns++;maxActiveTurns=Math.max(maxActiveTurns,activeTurns);}
      const counted=running;
      try{return await originalTurn.apply(this,args);}catch(error){if(counted)turnErrors++;throw error;}
      finally{if(counted)activeTurns--;}
    };
    win.HTMLCanvasElement.prototype.toBlob=function(callback,...args){
      const begin=now(),width=this.width,height=this.height;
      const candidates=[...pendingExports.values()],owner=candidates.length===1?candidates[0]:null;
      if(candidates.length>1)for(const job of candidates)job.rastersAmbiguous=true;
      return originalToBlob.call(this,blob=>{if(owner&&running)owner.rasters.push({width,height,bytes:blob?.size??null,toBlobMs:now()-begin});callback(blob);},...args);
    };
    win.URL.createObjectURL=function(blob){const url=originalCreate.call(this,blob);urls.set(url,{created:now(),type:blob.type,bytes:blob.size});maxUrls=Math.max(maxUrls,urls.size);
      const png=pendingExports.get('png');
      if(running&&png&&blob.type==='image/png'){
        png.elapsedMs=now()-png.started;png.bytes=blob.size;png.completed=true;
        exports.push(png);pendingExports.delete('png');
      }return url;};
    win.URL.revokeObjectURL=function(url){urls.delete(url);return originalRevoke.call(this,url);};
    const exportClick=event=>{const id=event.target.closest('button')?.id;
      if(!running||!['brandExportPng','brandExportPackage'].includes(id)||event.target.closest('button').disabled)return;
      const kind=id==='brandExportPng'?'png':'zip';
      if(pendingExports.has(kind)){events.push({type:'duplicate-export-action',kind,atMs:elapsed()});return;}
      if(pendingExports.size)events.push({type:'overlapping-exports',kind,atMs:elapsed()});
      pendingExports.set(kind,{kind,started:now(),atMs:elapsed(),order:Number(doc.querySelector('#brandOrder').value),size:Number(doc.querySelector('#brandPngSize').value),rasters:[]});};
    doc.addEventListener('click',exportClick,true);
    const exportObserver=new win.MutationObserver(()=>{
      const job=pendingExports.get('zip');
      if(running&&job&&doc.querySelector('#brandExportPackage').getAttribute('aria-busy')==='false'){
        const link=doc.querySelector('#brandPackageSave');job.elapsedMs=now()-job.started;
        job.completed=!link.hidden;job.downloadCharacters=link.getAttribute('href')?.length??0;
        job.status=doc.querySelector('#brandPackageStatus').textContent;
        exports.push(job);pendingExports.delete('zip');
      }
    });
    exportObserver.observe(doc.querySelector('#brandExportPackage'),{attributes:true,attributeFilter:['aria-busy']});
    const visible=()=>doc.visibilityState==='visible'&&!doc.querySelector('#brandWorkspace').hidden;
    const visibility=()=>{lastFrame=null;if(running)events.push({type:'document-visibility',atMs:elapsed(),value:doc.visibilityState});};
    doc.addEventListener('visibilitychange',visibility);
    function sample(final=false){
      if(!running)return;
      const logo=preview?.logo||observedLogo,memory=win.performance.memory;
      const row={atMs:final?ended-started:elapsed(),final,visible:visible(),dom:doc.querySelectorAll('*').length,cubies:doc.querySelectorAll('#brandRotor .cubie').length,
        layers:doc.querySelectorAll('#brandRotor .cube-turn-layer').length,animations:doc.getAnimations().length,
        heapUsed:memory?.usedJSHeapSize??null,heapTotal:memory?.totalJSHeapSize??null,
        order:logo?.state.order??null,cycles:logo?.completedCycles??null,journal:logo?.history.length??null,
        task:!!logo?._task,recovery:!!logo?._recovery,paused:logo?.paused??null,stage:logo?.stage??null,activeTurns,urls:urls.size};
      if(samples.length<256)samples.push(row);else samples[samples.length-1]=row;
      if(!final)timer=win.setTimeout(sample,sampleIntervalMs);
    }
    function frame(stamp){if(!running)return;if(visible()){if(lastFrame!==null)frames.add(stamp-lastFrame);lastFrame=stamp;}else lastFrame=null;raf=win.requestAnimationFrame(frame);}
    function recordLongTasks(entries){
      for(const entry of entries){if(entry.startTime<started||(ended&&entry.startTime>ended))continue;longTaskCount++;longTaskTotal+=entry.duration;longTaskMax=Math.max(longTaskMax,entry.duration);
        if(longTasks.length<128)longTasks.push({atMs:entry.startTime-started,durationMs:entry.duration});}
    }
    const clone=value=>JSON.parse(JSON.stringify(value));
    function collectSnapshot(){const logo=preview?.logo||observedLogo;return {formatVersion:2,running,startedAtMs:started,endedAtMs:ended,elapsedMs:elapsed(),requestedDurationMs:durationMs,turns,maxActiveTurns,turnErrors,frames:frames.summary(),samples,events,exports,
      sampleCoverage:{intervalMs:sampleIntervalMs,limit:256,firstAtMs:samples[0]?.atMs??null,lastAtMs:samples.at(-1)?.atMs??null},
      incompleteExports:[...pendingExports.values()].map(job=>({...job,elapsedMs:(ended||now())-job.started,completed:false})),
      longTasks:{supported:win.PerformanceObserver.supportedEntryTypes.includes('longtask'),count:longTaskCount,totalMs:longTaskTotal,maxMs:longTaskMax,entries:longTasks},
      temporaryUrls:{active:urls.size,max:maxUrls},preview:logo?{order:logo.state.order,cycles:logo.completedCycles,journal:logo.history,stage:logo.stage,paused:logo.paused,busy:logo.busy,solved:logo.state.isSolved()}:null,
      environment:{userAgent:win.navigator.userAgent,width:win.innerWidth,height:win.innerHeight,dpr:win.devicePixelRatio,visibility:doc.visibilityState,reduced:win.matchMedia('(prefers-reduced-motion: reduce)').matches}};}
    function stop(){if(!running)return;ended=now();sample(true);running=false;win.cancelAnimationFrame(raf);win.clearTimeout(timer);win.clearTimeout(stopTimer);
      if(observer)recordLongTasks(observer.takeRecords());observer?.disconnect();events.push({type:'measurement-ended',atMs:ended-started});finalSnapshot=clone(collectSnapshot());}
    const api={installed:true,start({order,duration=600000}={}){
      if(disposed||hasStarted||![2,3].includes(order)||!Number.isFinite(duration)||duration<1000)throw new Error('One measurement per page; use order 2 or 3 and a positive duration.');
      if(Number(doc.querySelector('#brandOrder').value)!==order)throw new Error('Select the measured cube order first.');
      running=true;hasStarted=true;started=now();durationMs=duration;sampleIntervalMs=Math.max(2500,Math.ceil(duration/254));
      if(win.PerformanceObserver.supportedEntryTypes.includes('longtask')){observer=new win.PerformanceObserver(list=>recordLongTasks(list.getEntries()));observer.observe({type:'longtask',buffered:false});}
      events.push({type:'measurement-started',atMs:0,order});sample();raf=win.requestAnimationFrame(frame);stopTimer=win.setTimeout(stop,duration);
    },mark(label){if(running)events.push({type:'action',label,atMs:elapsed()});},stop,
    snapshot(){return clone(finalSnapshot||collectSnapshot());},
    dispose(){if(disposed)return;stop();disposed=true;exportObserver.disconnect();doc.removeEventListener('click',exportClick,true);doc.removeEventListener('visibilitychange',visibility);
      win.EvaBrandStudio.Preview.prototype.run=originalRun;win.CubeRendering.CubeAnimator.prototype.turn=originalTurn;
      win.CubeLogoSystem.CubeLogo.prototype._emit=originalEmit;
      win.URL.createObjectURL=originalCreate;win.URL.revokeObjectURL=originalRevoke;win.HTMLCanvasElement.prototype.toBlob=originalToBlob;api.installed=false;}};
    return api;
  }
  return Object.freeze({histogram,install});
});
