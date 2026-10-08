// Portable, offline brand delivery. Geometry and asset content remain owned by EvaBrand.
(function(root, factory) {
  const common=typeof module === "object" && module.exports;
  const api=factory(common?require("./logo-tokens.js"):root.EvaBrand,
    common?require("./brand-assets.js"):root.EvaBrandAssets,
    common?require("./brand-identity.js"):root.EvaBrandIdentity);
  if(common)module.exports=api;else root.EvaBrandPackage=api;
})(globalThis, function(brand, assets, identity) {
  "use strict";
  const encoder=new TextEncoder();
  const componentNames=Object.freeze(["brand-identity.js","logo-tokens.js","cube-state.js","cube-animator.js","cube-logo.js","cube-geometry.css","brand-assets.js","ui-system.css","ui-theme.js"]);
  const crcTable=new Uint32Array(256);
  for(let i=0;i<256;i++){
    let value=i;
    for(let bit=0;bit<8;bit++)value=(value>>>1)^((value&1)?0xedb88320:0);
    crcTable[i]=value>>>0;
  }
  function bytes(data){
    if(typeof data==="string")return encoder.encode(data);
    if(data instanceof Uint8Array)return data;
    if(data instanceof ArrayBuffer)return new Uint8Array(data);
    throw new TypeError("Archive content must be text or bytes");
  }
  function crc32(data){
    let crc=0xffffffff;
    for(const value of bytes(data))crc=(crc>>>8)^crcTable[(crc^value)&255];
    return (crc^0xffffffff)>>>0;
  }
  function safeName(name){
    if(typeof name!=="string"||!name||name.startsWith("/")||/[\\:\x00-\x1f]/.test(name)||
      name.split("/").some(part=>!part||part===".."||part==="."))throw new RangeError("Unsafe archive path");
    if(encoder.encode(name).length>65535)throw new RangeError("Archive path is too long");
    return name;
  }
  function zip(files){
    if(!Array.isArray(files)||files.length>65535)throw new RangeError("Too many archive entries");
    const names=new Set(),parts=[],central=[];let offset=0,centralSize=0;
    for(const file of files){
      const name=encoder.encode(safeName(file.name)),data=bytes(file.data),checksum=crc32(data);
      if(names.has(file.name))throw new RangeError("Duplicate archive path");
      names.add(file.name);
      const local=new Uint8Array(30+name.length),lv=new DataView(local.buffer);
      lv.setUint32(0,0x04034b50,true);lv.setUint16(4,20,true);lv.setUint16(6,0x800,true);
      lv.setUint16(12,0x2821,true);lv.setUint32(14,checksum,true);
      lv.setUint32(18,data.length,true);lv.setUint32(22,data.length,true);lv.setUint16(26,name.length,true);
      local.set(name,30);
      const header=new Uint8Array(46+name.length),cv=new DataView(header.buffer);
      cv.setUint32(0,0x02014b50,true);cv.setUint16(4,20,true);cv.setUint16(6,20,true);
      cv.setUint16(8,0x800,true);cv.setUint16(14,0x2821,true);cv.setUint32(16,checksum,true);
      cv.setUint32(20,data.length,true);cv.setUint32(24,data.length,true);cv.setUint16(28,name.length,true);
      cv.setUint32(42,offset,true);header.set(name,46);
      parts.push(local,data);central.push(header);offset+=local.length+data.length;centralSize+=header.length;
      if(offset+centralSize+22>0xffffffff)throw new RangeError("Archive exceeds ZIP size limit");
    }
    const end=new Uint8Array(22),ev=new DataView(end.buffer);
    ev.setUint32(0,0x06054b50,true);ev.setUint16(8,files.length,true);ev.setUint16(10,files.length,true);
    ev.setUint32(12,centralSize,true);ev.setUint32(16,offset,true);
    const result=new Uint8Array(offset+centralSize+22);let at=0;
    for(const part of [...parts,...central,end]){result.set(part,at);at+=part.length;}
    return result;
  }
  // A self-contained download URL keeps the completed kit usable without a short-lived Blob URL.
  function downloadUrl(archive){
    const data=bytes(archive),parts=[];
    for(let i=0;i<data.length;i+=32768)parts.push(String.fromCharCode(...data.subarray(i,i+32768)));
    return "data:application/zip;base64,"+btoa(parts.join(""));
  }
  function selection({order=2,variant="primary",view="iso",size=1024}={}){
    brand.geometry(order);brand.palette(variant);brand.camera(view);
    if(![256,512,1024,2048].includes(size))throw new RangeError("Unsupported PNG size");
    return {order:variant==="favicon"?2:order,variant,view:variant==="favicon"?"iso":view,size};
  }
  function catalog(){
    const entries=[];
    for(const order of [2,3])for(const variant of brand.variants.filter(v=>v!=="favicon")){
      for(const view of Object.keys(brand.tokens.geometry.views))entries.push({
        name:`svg/${order}x${order}/${variant}/${view}.svg`,options:{order,variant,view,size:256},
      });
    }
    for(const size of [16,32,48])entries.push({name:`icons/favicon-${size}.svg`,options:{order:2,variant:"favicon",view:"iso",size}});
    return entries;
  }
  function pngBytes(content,size){
    const data=bytes(content),signature=[137,80,78,71,13,10,26,10];
    if(data.length<33||!signature.every((value,i)=>data[i]===value))throw new TypeError("Invalid PNG content");
    const view=new DataView(data.buffer,data.byteOffset,data.byteLength);
    let at=8,hasPixels=false;
    while(at+12<=data.length){
      const length=view.getUint32(at),end=at+12+length;
      if(end>data.length)throw new TypeError("Truncated PNG chunk");
      const type=String.fromCharCode(...data.subarray(at+4,at+8));
      if(crc32(data.subarray(at+4,end-4))!==view.getUint32(end-4))throw new TypeError("Invalid PNG checksum");
      if(at===8&&(type!=="IHDR"||length!==13||view.getUint32(at+8)!==size||view.getUint32(at+12)!==size))throw new RangeError("PNG size does not match selection");
      if(type==="IDAT")hasPixels=true;
      if(type==="IEND"){
        if(length!==0||end!==data.length||!hasPixels)throw new TypeError("Incomplete PNG content");
        return data;
      }
      at=end;
    }
    throw new TypeError("Incomplete PNG content");
  }
  function componentCss(){
    const [x,y]=brand.camera();
    return `@import url('./cube-geometry.css');
.eva-logo{--hero-x:${x}deg;--hero-y:${y}deg;--cube-perspective:var(--brand-perspective);--scene-scale:1;
--cube-white:var(--brand-cube-white);--cube-white-shade:var(--brand-cube-white-shade);
--cube-graphite:var(--brand-cube-graphite);--cube-graphite-shade:var(--brand-cube-graphite-shade);
--cube-edge:var(--brand-cube-edge);--cube-top:var(--brand-cube-white);--cube-top-shade:var(--brand-cube-white-shade);}
@media(prefers-reduced-motion:reduce){.eva-logo *{transition:none!important;}}
`;
  }
  function previewHtml(entries,selected){
    const tiles=entries.map(entry=>`<figure><div class="asset-image" style="background:${brand.palette(entry.options.variant).background}"><img src="${entry.name}" alt="EVA ${entry.options.order}x${entry.options.order} ${entry.options.variant} ${entry.options.view}"/></div><figcaption>${entry.name}</figcaption></figure>`).join("\n");
    return `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EVA Brand Kit</title>
<link rel="icon" href="icons/favicon-32.svg"><link rel="stylesheet" href="components/eva-logo.css"><link rel="stylesheet" href="components/ui-system.css"><script src="components/ui-theme.js"></script>
<style>body{margin:0;padding:var(--ui-space-8);line-height:1.7}main{max-width:1100px;margin:auto}h1{font-weight:550}button,select{padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);background:var(--ui-panel);color:var(--ui-text)}button:disabled{opacity:.45}.demo{padding:var(--ui-space-6);border:1px solid var(--ui-border);border-radius:var(--ui-radius-card);background:var(--ui-panel)}.demo .cube-stage{height:260px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:var(--ui-space-3)}figure{margin:0;padding:var(--ui-space-3);border-radius:var(--ui-radius-card);border:1px solid var(--ui-border);background:var(--ui-panel)}.asset-image{border-radius:var(--ui-radius-control);padding:var(--ui-space-3)}img{display:block;width:120px;height:120px;max-width:100%;margin:auto}figcaption{font-size:var(--ui-type-caption);color:var(--ui-muted);overflow-wrap:anywhere;margin-top:var(--ui-space-3)}#status{min-height:24px;color:var(--ui-muted)}@media(max-width:500px){body,.demo{padding:var(--ui-space-4)}button,select{min-height:var(--ui-touch-height)}}</style></head><body>
<a class="workspace-skip" href="#previewContent">跳到预览内容 / Skip to preview</a><main id="previewContent"><h1>EVA · Brand Kit</h1><p>Brand ${identity.versions.brandVersion} · Components ${identity.versions.componentVersion} · Tokens schema ${identity.versions.tokenSchemaVersion} · Package format ${identity.versions.formatVersion}</p><p>离线品牌资产预览 / Offline identity preview · ${selected.order} × ${selected.order} · ${selected.variant} · ${selected.view}</p>
<section class="appearance-settings" aria-labelledby="appearanceHeading"><h2 id="appearanceHeading">统一外观 / Shared appearance</h2><label for="themePreference">主题模式 / Theme mode</label><select id="themePreference" data-theme-preference><option value="system">跟随系统 / System</option><option value="light">浅色 / Light</option><option value="dark">深色 / Dark</option></select><p data-theme-status role="status" aria-live="polite"></p></section>
<section class="demo"><h2>真实动态组件 / Live component</h2><label for="order">结构 / Order</label> <select id="order"><option value="2">2 × 2</option><option value="3">3 × 3</option></select>
<div class="cube-stage eva-logo" data-phase="5"><div class="cube-camera"><div class="cube-model"><div class="cube-rotor" id="rotor" aria-hidden="true"></div></div></div></div>
<button id="play">∞ 合法循环 / Play loop</button> <button id="solve">复原 / Solve</button><p id="status" aria-live="off">标准状态 / Ready</p><p id="motionAnnouncement" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></p></section>
<h2>标准资产 / Solved assets</h2><p><a href="selected/eva-selected.svg">当前选择 / Selected mark</a> · <a href="manifest.json">文件清单 / Manifest</a> · <a href="README.md">使用规范 / Usage</a> · <a href="CHANGELOG.md">变更记录 / Changelog</a></p><div class="grid">${tiles}</div></main>
<script src="components/logo-tokens.js"></script><script src="components/cube-state.js"></script><script src="components/cube-animator.js"></script><script src="components/cube-logo.js"></script>
<script>
const rotor=document.getElementById('rotor'),order=document.getElementById('order'),status=document.getElementById('status'),announcement=document.getElementById('motionAnnouncement'),media=matchMedia('(prefers-reduced-motion: reduce)');
let logo,revision=0,playing=false,switching=false;
function announce(text){if(announcement.textContent!==text)announcement.textContent=text;}
function renderStatus(s){status.textContent=(s.paused?'Paused · ':'')+s.stage+(s.move?' · '+s.index+'/'+s.total+' · '+s.move:'');if(s.stage==='error'){playing=false;announce('动效已停止，动作记录已保留 / Animation stopped. Move journal preserved.');}else if(s.stage==='idle'&&s.solved&&!playing&&!switching)announce('已回到标准状态 / Returned to the standard state.');}
function mount(value){rotor.replaceChildren();const state=new CubeLogic.CubeState(value),animator=new CubeRendering.CubeAnimator(rotor,state);logo=new CubeLogoSystem.CubeLogo({state,animator,...EvaBrand.tokens.motion.modes[value===3?'deep':'normal'],onChange:renderStatus});logo.setReducedMotion(media.matches).catch(report);if(document.hidden)logo.pause('hidden');document.getElementById('play').disabled=media.matches;}
function report(error){playing=false;status.textContent='操作未完成 / '+error.message;announce('操作未完成，请检查状态后重试 / Operation interrupted. Check the state and try again.');}
order.addEventListener('change',async()=>{const wanted=Number(order.value),current=++revision;playing=false;switching=true;announce('正在切换模型 / Switching cube model.');try{await logo.solve();if(current===revision){mount(wanted);announce('模型已切换 / Cube model changed.');}}catch(error){report(error);}finally{if(current===revision)switching=false;}});
document.getElementById('play').addEventListener('click',()=>{playing=true;announce('连续演示已开始 / Continuous demonstration started.');logo.play({loop:true}).catch(report);});
document.getElementById('solve').addEventListener('click',()=>{playing=false;announce('正在逆序复原 / Restoring committed moves.');logo.solve().catch(report);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)logo.pause('hidden');else{logo.resume('hidden');if(playing)announce('连续演示继续 / Continuous demonstration resumed.');}});
media.addEventListener('change',()=>{document.getElementById('play').disabled=media.matches;if(media.matches){playing=false;announce('已减少动态效果 / Reduced motion enabled.');}logo.setReducedMotion(media.matches).catch(report);});
order.value='${selected.order}';mount(${selected.order});
</script></body></html>`;
  }
  function usage(selected,pngFiles){
    return `# EVA Brand Kit / 品牌交付包

Brand ${identity.versions.brandVersion} · Components ${identity.versions.componentVersion} · Token schema ${identity.versions.tokenSchemaVersion} · Package format ${identity.versions.formatVersion}

打开 preview.html 可离线查看所有静态资产，并运行真实的二阶 / 三阶动态组件。没有外部字体、网络依赖或模型调用。

## 资产

- svg/：二阶和三阶，Primary、Monochrome、Dark、Light，六种观察角；共 48 个标准 SVG。
- icons/：16、32、48px 二阶三视角微标。
- selected/eva-selected.svg：当前选择 ${selected.order} × ${selected.order} / ${selected.variant} / ${selected.view}。
- PNG：${pngFiles.length?pngFiles.join(', '):'此包由 CLI 生成；在品牌工作区下载可取得 PNG。'}
- tokens.json：品牌 Token；manifest.json：版本、规范化规则／几何／Token 指纹、组件和文件 SHA-256、字节数与 CRC32。CHANGELOG.md：品牌变更记录。
- components/：原生 JavaScript 动态组件和几何样式；preview.html 同时提供接入示例。
- components/ui-system.css、ui-theme.js：与产品相同的呈现 Token 和浅色／深色／系统主题；文件全部随包交付。

## 接入

按顺序加载 components/logo-tokens.js、cube-state.js、cube-animator.js、cube-logo.js，并引用 eva-logo.css。
根节点使用 cube-stage eva-logo 类和 data-phase="5"，内部结构参照 preview.html。
创建 CubeState、CubeAnimator 和 CubeLogo 后显式调用 play({loop:true})；solve() 严格逆序撤销已提交动作。
动效失败后保留状态与日志，使用 recover({createAnimator}) 核对日志、重建呈现器并逆序复原；工厂必须沿用传入的状态对象。
日志不一致或恢复再次失败时保持错误，显示独立静态标识；不直接重置坐标或重复启动循环。重复重试共享一个任务。
正常对话采用二阶，深度思考采用三阶。响应完成、结构切换及退出演示前应等待 solve()。
遵循 prefers-reduced-motion，并在页面隐藏时 pause('hidden')，显示时 resume('hidden')。
预览主题更换只更新呈现，不重建魔方或改变动作日志。浏览器文件来源的存储能力不同，存储不可用时主题仍在当前页面生效并显示提示。
从 SVG / PNG 取得静态品牌资产，不截取打乱中的动态帧。

## 使用规范

L1 动态主标：160px 以上；L2 产品标识：48–160px；L3 微标：16–48px。
保持整体比例；四周留白至少为标识宽度的 25%。SVG / PNG 已包含留白。
允许主题变体、单色、等比缩放、静态展示和合法转动。
禁止拉伸、独立缩放模块、随意修改间隙与圆角、添加霓虹或复杂纹理。
动态演示结束必须复原。指纹用于内容与版本追踪；没有签名，不证明来源身份或官方授权。CRC32 用于检测文件损坏。
格式 1 为无来源指纹的旧包；格式 2 增加来源追踪。未知格式／Token schema 或不支持的品牌与组件主版本应拒绝，不猜测迁移。
`;
  }
  function assemble({selected:input,components,pngFiles=[]}={}){
    const selected=selection(input),entries=catalog(),files=[];
    for(const name of componentNames){
      if(typeof components?.[name]!=="string"||!components[name])throw new TypeError(`Missing component: ${name}`);
    }
    for(const entry of entries)files.push({name:entry.name,data:assets.svg(entry.options)});
    files.push({name:"selected/eva-selected.svg",data:assets.svg(selected)});
    const allowedPng=new Set(["selected/eva-selected.png",...[16,32,48].map(size=>`icons/favicon-${size}.png`)]);
    if(pngFiles.length!==0&&pngFiles.length!==allowedPng.size)throw new RangeError("PNG delivery must include the selected mark and all three icons");
    for(const file of pngFiles){
      if(!allowedPng.has(file.name))throw new RangeError("Unknown PNG asset");
      const size=file.name.startsWith("selected/")?selected.size:Number(file.name.match(/(\d+)\.png$/)[1]);
      const data=pngBytes(file.data,size);
      files.push({name:file.name,data});
    }
    for(const name of componentNames)files.push({name:"components/"+name,data:components[name]});
    files.push({name:"components/eva-logo.css",data:componentCss()},
      {name:"tokens.json",data:JSON.stringify(brand.tokens,null,2)+"\n"},
      {name:"README.md",data:usage(selected,pngFiles.map(file=>file.name))},
      {name:"CHANGELOG.md",data:identity.changelog()},
      {name:"preview.html",data:previewHtml(entries,selected)});
    const manifest={...identity.versions,brand:"EVA",selected,clearSpaceRatio:brand.tokens.usage.clearSpaceRatio,
      provenance:sourceProvenance(components),
      assetState:"solved",pngIncluded:pngFiles.length>0,
      files:files.map(file=>({path:file.name,bytes:bytes(file.data).length,crc32:crc32(file.data).toString(16).padStart(8,"0"),sha256:identity.sha256(bytes(file.data))}))};
    files.push({name:"manifest.json",data:JSON.stringify(manifest,null,2)+"\n"});
    // Validate uniqueness before presenting a completed manifest or initiating a download.
    const names=new Set();for(const file of files){safeName(file.name);if(names.has(file.name))throw new RangeError("Duplicate archive path");names.add(file.name);}
    return {files,manifest};
  }
  function sourceProvenance(components){
    for(const name of componentNames)if(typeof components?.[name]!=="string"||!components[name])throw new TypeError(`Missing component: ${name}`);
    return identity.provenance(brand,Object.fromEntries([...componentNames.map(name=>[name,components[name]]),['eva-logo.css',componentCss()]]));
  }
  function assertSource(record,components){
    identity.inspectManifest(record);
    for(const [key,value] of Object.entries(identity.versions))if(record[key]!==value)throw new Error('Brand sources changed. Rebuild assets and reload the workspace.');
    if(identity.canonical(record.provenance)!==identity.canonical(sourceProvenance(components)))throw new Error('Brand sources changed. Rebuild assets and reload the workspace.');
  }
  return Object.freeze({componentNames,catalog,selection,assemble,zip,downloadUrl,crc32,sourceProvenance,assertSource,versions:identity.versions,inspectManifest:identity.inspectManifest});
});
