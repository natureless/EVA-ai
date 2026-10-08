(function(root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./logo-tokens.js") : root.EvaBrand,
    common ? require("./cube-state.js") : root.CubeLogic,
    common ? require("./cube-logo.js") : root.CubeLogoSystem,
    common ? require("./brand-config.js") : root.EvaBrandConfig);
  if (common) module.exports = api;
  else { root.EvaBrandStudio = api; api.init(); }
})(globalThis, function(brand, logic, motion, config) {
  "use strict";
  const {defaults,pngSizes,preferences}=config;
  const storageKey = "eva-brand-workspace";

  function readPreferences(storage) {
    try {
      const saved = storage.getItem(storageKey);
      if (saved) return preferences(JSON.parse(saved));
      return preferences({variant:storage.getItem("eva-brand-variant")});
    } catch (_) { return preferences(); }
  }
  function writePreferences(storage, value) {
    try { storage.setItem(storageKey, JSON.stringify(preferences(value))); return true; }
    catch (_) { return false; }
  }

  // A generated file describes the selection captured at click time, never the later preview.
  class KitDelivery {
    constructor() {this.pending=null;this.ready=null;}
    start(selection) {
      if(this.pending)throw new Error("A kit is already being prepared");
      this.pending=Object.freeze(Object.fromEntries(["order","variant","view","size"].map(key=>[key,selection[key]])));
      return this.pending;
    }
    finish(file) {
      if(!this.pending)throw new Error("No kit is being prepared");
      this.ready=Object.freeze({...file,selection:this.pending});this.pending=null;
      return this.ready;
    }
    fail() {this.pending=null;}
    matches(selection) {
      return !!this.ready&&["order","variant","view","size"].every(key=>this.ready.selection[key]===selection[key]);
    }
  }

  // Geometry can be replaced only after the committed journal has been reversed.
  class Preview {
    constructor({mount, onChange=()=>{}, order=2}) {
      this.mount=mount; this.onChange=onChange; this.revision=0;
      this.hidden=false; this.reduced=false; this.userPaused=false;
      this.failed=false; this.recovering=null;
      this.replace(order);
    }
    replace(order) {
      this.failed=false;
      this.order=order; this.state=new logic.CubeState(order); this.animator=this.mount(this.state);
      this.userPaused=false;
      this.logo=new motion.CubeLogo({state:this.state, animator:this.animator,
        ...brand.tokens.motion.modes[order===3?"deep":"normal"], onChange:s=>{
          if (s.stage==="error") this.failed=true;
          this.onChange(s);
        }});
      if (this.hidden) this.logo.pause("hidden");
      if (this.reduced) this.logo.setReducedMotion(true);
    }
    async selectOrder(order) {
      brand.geometry(order);
      if (this.failed) throw new Error("Retry the failed preview before changing geometry");
      const rev=++this.revision; this.userPaused=false;
      await this.logo.solve();
      if (rev!==this.revision) return false;
      if (order!==this.order) this.replace(order);
      return true;
    }
    async run(action) {
      if (!["idle","scramble","solve","cycle","continuous"].includes(action)) throw new RangeError("Unknown motion");
      if (this.failed) throw new Error("Retry the failed preview before playing");
      const rev=++this.revision; this.userPaused=false;
      await this.logo.solve();
      if (rev!==this.revision || this.reduced) return;
      if (action==="scramble") await this.logo.scramble();
      if (action==="cycle" || action==="continuous") await this.logo.play({loop:action==="continuous"});
    }
    togglePause() {
      if (this.failed || !this.logo.busy || this.reduced) return;
      this.userPaused=!this.userPaused;
      this.userPaused ? this.logo.pause("user") : this.logo.resume("user");
    }
    setHidden(value) {
      this.hidden=value;
      value ? this.logo.pause("hidden") : this.logo.resume("hidden");
    }
    async setReduced(value) {
      this.reduced=value; this.userPaused=false; ++this.revision;
      await this.logo.setReducedMotion(value);
    }
    recover() {
      if (this.recovering) return this.recovering;
      if (!this.failed) return Promise.resolve();
      ++this.revision;
      this.userPaused=false;
      this.recovering=this.logo.recover({createAnimator:state=>this.mount(state)})
        .then(()=>{this.animator=this.logo.animator;this.failed=false;})
        .finally(()=>{this.recovering=null;});
      return this.recovering;
    }
  }

  function init() {
    const $=id=>document.getElementById(id), stage=$("brandPreviewStage");
    if (!stage) return;
    const variant=$("brandVariant"), order=$("brandOrder"), status=$("brandMotionStatus"),
      image=$("brandStaticMark"), showStatic=$("brandStatic"), grid=$("brandTokenGrid"),
      reduced=$("brandReduced"), note=$("brandFeedback"), pause=$("brandPause"), pngSize=$("brandPngSize");
    const media=window.matchMedia("(prefers-reduced-motion: reduce)");
    const en=()=>typeof I18N!=="undefined" && I18N.lang()==="en";
    const t=(zh,english)=>en()?english:zh;
    const variantLabels={primary:["主标","Primary"],monochrome:["单色","Monochrome"],dark:["深色背景","Dark"],light:["浅色背景","Light"],favicon:["微标","Micro mark"]};
    let storage;
    try { storage=localStorage; } catch (_) { /* Preview remains usable without persistent storage. */ }
    let prefs=readPreferences(storage), active={...prefs}, runtimeTab=false, lastStatus=null,
      configPending=0, exporting=false, packaging=false, feedback=null, packageFeedback=null;
    let configDraft=null,configDraftRevision=0,configFeedback=null,fileRevision=0,configInvalid=false,configRowsKey=null;
    let retrying=false;
    const delivery=new KitDelivery();
    let motionAnnouncement=["动效已就绪。","Motion controls are ready."];
    function announce(zh,english) {
      motionAnnouncement=[zh,english];
      const el=$("brandMotionAnnouncement"), text=t(zh,english);
      if (el && el.textContent!==text) el.textContent=text;
    }
    const names={idle:["标准状态","Ready"], intro:["准备旋转","Starting"], scrambling:["合法打乱","Scrambling"],
      scrambled:["打乱完成 · 可逆序复原","Scrambled · Ready to solve"], solving:["逆序复原","Solving"],
      settling:["回到标准姿态","Settling"], error:["动效中断","Animation stopped"]};

    function inform(zh,english) { feedback=[zh,english]; note.textContent=t(zh,english); }
    function renderStatus(s) {
      lastStatus=s;
      status.textContent=(s.paused ? t("已暂停 · ","Paused · ") : "") +
        (names[s.stage]||names.idle)[en()?1:0] + (s.move ? ` · ${s.index}/${s.total} · ${s.move}` : "");
      stage.dataset.solved=String(s.solved); stage.dataset.motion=s.stage;
      $("brandJournal").textContent=s.solveMoves.join(" ") || "—";
      updatePause();
      if (s.stage==="error") { announce("动效已停止，动作记录已保留。","Animation stopped. The move journal is preserved."); refresh(); }
    }
    const preview=new Preview({order:prefs.order, mount:state=>{
      $("brandRotor").replaceChildren();
      return new CubeRendering.CubeAnimator($("brandRotor"),state);
    }, onChange:renderStatus});
    const display=new config.Controller({initial:prefs,
      prepare:(value,current)=>config.preparePreview(preview,value,current,media.matches),
      commit:value=>{active={...value};return save();},
      changed:s=>{prefs={...s.desired};active={...s.active};configPending=s.pending;syncControls();refresh();}});

    function updatePause() {
      pause.textContent=preview.userPaused ? t("继续","Resume") : t("暂停","Pause");
      pause.setAttribute("aria-pressed",String(preview.userPaused));
      // The final idle notification is emitted before the task Promise is released.
      pause.disabled=preview.failed || !!configPending || preview.reduced || active.static || active.variant==="favicon" ||
        !lastStatus || ["idle","error"].includes(lastStatus.stage);
    }
    function syncControls() {
      variant.value=prefs.variant; order.value=String(prefs.order);
      showStatic.checked=prefs.static; reduced.checked=prefs.reduced||media.matches;
      reduced.disabled=media.matches; pngSize.value=String(prefs.pngSize);
    }
    function options() {
      return {order:active.variant==="favicon"?2:preview.order, variant:active.variant,
        view:active.variant==="favicon"?"iso":active.view};
    }
    function selectionLabel(selected) {
      const views={front:["正面","Front"],side:["侧面","Side"],top:["顶部","Top"],iso:["三视角","Isometric"],oblique:["斜视图","Oblique"],bottom:["底部","Bottom"]};
      return `${selected.order} × ${selected.order} · ${t(...variantLabels[selected.variant])} · ${t(...views[selected.view])} · ${selected.size}px`;
    }
    function renderDelivery(selected) {
      $("brandSelectionSummary").textContent=t("当前选择：","Current selection: ")+selectionLabel(selected);
      const record=delivery.ready,summary=$("brandPackageSelection"),link=$("brandPackageSave");
      summary.hidden=!record;link.hidden=!record;
      if(!record)return;
      summary.textContent=t("已生成文件：","Generated file: ")+selectionLabel(record.selection);
      const matches=delivery.matches(selected);
      summary.dataset.matches=String(matches);
      $("brandPackageFreshness").hidden=false;
      const message=matches
        ? t("文件与当前选择一致。","The file matches the current selection.")
        : t("当前选择已变化；保存链接仍是上次生成的文件。请重新生成以取得当前选择。","Selection changed. The save link still contains the previous file. Generate again for the current selection.");
      if($("brandPackageFreshness").textContent!==message)$("brandPackageFreshness").textContent=message;
      $("brandPackageFreshness").dataset.matches=String(matches);
      link.textContent=matches?t("保存已生成的 ZIP","Save generated ZIP"):t("保存上次生成的 ZIP","Save previous ZIP");
      link.href=record.href;link.download=record.name;
    }
    function refresh() {
      const selected=options(), g=brand.geometry(selected.order), p=brand.palette(selected.variant);
      const [x,y]=brand.camera(selected.view), isStatic=preview.failed||active.static||selected.variant==="favicon";
      order.disabled=active.variant==="favicon";
      stage.dataset.variant=selected.variant; stage.dataset.static=String(isStatic);
      stage.dataset.view=selected.view; stage.setAttribute("aria-busy",String(!!configPending));
      $("brandRecovery").hidden=!preview.failed;
      $("brandRetry").disabled=retrying;
      $("brandRetry").textContent=retrying?t("正在恢复…","Restoring…"):t("重试动效","Retry animation");
      $("brandRecoveryNote").textContent=t("动效已停止，暂用标准静态标识。动作记录已保留。","Animation stopped. A standard static mark is shown. The move journal is preserved.");
      stage.style.setProperty("--hero-x",x+"deg"); stage.style.setProperty("--hero-y",y+"deg");
      stage.style.setProperty("--cube-perspective",brand.tokens.geometry.perspective+"px");
      for (const [key,value] of Object.entries({white:p.front,"white-shade":p.front,graphite:p.right,
        "graphite-shade":p.right,top:p.top,"top-shade":p.top,back:p.back,"back-shade":p.back,
        left:p.left,"left-shade":p.left,bottom:p.bottom,"bottom-shade":p.bottom,edge:p.edge})) {
        stage.style.setProperty("--cube-"+key,value);
      }
      image.src=EvaBrandAssets.dataUrl(selected);
      $("brandPreviewCaption").textContent=selected.variant==="favicon" ? "L3 / MICRO MARK · 16–48px" :
        `${isStatic?"L2 / PRODUCT MARK":"L1 / DYNAMIC MASTER"} · ${selected.order} × ${selected.order}`;
      grid.replaceChildren();
      [[t("系统结构","Structure"),`${selected.order} × ${selected.order} × ${selected.order} / ${selected.order===2?8:26} ${t("模块","cubies")}`],
        [t("尺寸 / 间隙 / 圆角","Size / gap / radius"),`${g.cubieSize} / ${g.gap} / ${g.radius}px`],
        [t("留白","Clear space"),"25%"],[t("观察角","View angle"),`${x}° / ${y}°`]]
        .forEach(([key,value])=>{
          const dt=document.createElement("dt"),dd=document.createElement("dd");
          dt.textContent=key; dd.textContent=value; grid.append(dt,dd);
        });
      document.querySelectorAll("[data-brand-motion]").forEach(b=>{
        b.disabled=!!configPending||isStatic||preview.reduced;
      });
      document.querySelectorAll("[data-brand-view]").forEach(b=>{
        b.setAttribute("aria-pressed",String(b.dataset.brandView===selected.view));
        b.disabled=selected.variant==="favicon";
      });
      for (const id of ["brandExportSvg","brandExportPng"]) $(id).disabled=!!configPending||(id==="brandExportPng"&&exporting);
      $("brandExportPackage").disabled=!!configPending||packaging;
      $("brandExportPackage").setAttribute("aria-busy",String(packaging));
      $("brandExportPackage").textContent=packaging?t("正在打包…","Preparing kit…"):t("↓ 下载交付包 · ZIP","↓ Download kit · ZIP");
      $("brandPackageSummary").textContent=t("52 SVG · 4 PNG · 动态组件 · Token · 规范与预览","52 SVG · 4 PNG · Components · Tokens · Usage & preview");
      const versions=EvaBrandPackage.versions;
      $("brandPackageVersion").textContent=t(`品牌 ${versions.brandVersion} · 组件 ${versions.componentVersion} · Token 结构 ${versions.tokenSchemaVersion} · 包格式 ${versions.formatVersion}`,
        `Brand ${versions.brandVersion} · Components ${versions.componentVersion} · Token schema ${versions.tokenSchemaVersion} · Package format ${versions.formatVersion}`);
      renderDelivery({...selected,size:active.pngSize});
      if(packageFeedback&&$("brandPackageStatus").textContent!==t(...packageFeedback))$("brandPackageStatus").textContent=t(...packageFeedback);
      document.querySelectorAll("[data-brand-asset]").forEach(el=>{
        el.src=EvaBrandAssets.dataUrl({variant:el.dataset.brandAsset,size:256});
      });
      document.querySelectorAll("[data-brand-select]").forEach(el=>{
        el.setAttribute("aria-pressed",String(el.dataset.brandSelect===selected.variant));
      });
      if (lastStatus) {
        status.textContent=preview.failed
          ? (retrying?t("正在按记录逆序复原…","Restoring committed moves…"):t("动效中断 · 静态标识","Animation stopped · Static mark"))
          : (lastStatus.paused ? t("已暂停 · ","Paused · ") : "")+(names[lastStatus.stage]||names.idle)[en()?1:0]+
            (lastStatus.move ? ` · ${lastStatus.index}/${lastStatus.total} · ${lastStatus.move}` : "");
      }
      updatePause();
      announce(...motionAnnouncement);
      if (feedback) note.textContent=t(...feedback);
      renderConfig();
    }
    async function guard(fn) {
      try { return await fn(); }
      catch (_) {
        inform(preview.failed?"动效中断，请使用重试动效恢复。":"操作未完成，请复原后重试。",
          preview.failed?"Animation stopped. Use Retry animation to recover.":"Operation interrupted. Restore and try again.");
        refresh(); return false;
      }
    }
    function save() {
      const saved=writePreferences(storage,active);
      if (!saved) {
        inform("当前配置已应用，但浏览器无法保存。","Settings applied. Browser storage is unavailable.");
      }
      return saved;
    }
    function configure(patch,options) {return display.apply(preferences({...display.desired,...patch}),options);}
    variant.addEventListener("change",()=>guard(()=>configure({variant:variant.value})));
    order.addEventListener("change",()=>guard(()=>configure({order:Number(order.value)})));
    showStatic.addEventListener("change",()=>guard(()=>configure({static:showStatic.checked})));
    reduced.addEventListener("change",()=>guard(()=>configure({reduced:reduced.checked})));
    media.addEventListener("change",()=>guard(()=>configure({})));
    document.querySelectorAll("[data-brand-select]").forEach(b=>{
      b.addEventListener("click",()=>guard(()=>configure({variant:b.dataset.brandSelect})));
    });
    document.querySelectorAll("[data-brand-view]").forEach(b=>b.addEventListener("click",()=>{
      display.present({view:b.dataset.brandView});
    }));
    pngSize.addEventListener("change",()=>{
      display.present({pngSize:Number(pngSize.value)});
    });
    document.querySelectorAll("[data-brand-motion]").forEach(b=>{
      b.addEventListener("click",()=>guard(async()=>{
        announce("正在执行所选动效。","The selected motion is running.");
        await preview.run(b.dataset.brandMotion);
        announce("所选动效已完成。","The selected motion has finished.");
      }));
    });
    pause.addEventListener("click",()=>{
      preview.togglePause();
      announce(preview.userPaused?"动效已暂停。":"动效已继续。",preview.userPaused?"Animation paused.":"Animation resumed.");
    });
    $("brandRetry").addEventListener("click",()=>guard(async()=>{
      if (retrying) return;
      retrying=true;refresh();
      try {
        await preview.recover();
        if (await configure(prefs)) inform("已按动作记录逆序复原，可以重新播放。","Committed moves restored. The preview is ready to play again.");
      } finally {retrying=false;refresh();}
    }));
    $("brandReset").addEventListener("click",()=>guard(async()=>{
      if (await configure(defaults)) inform("已恢复默认配置。","Default settings restored.");
    }));
    document.addEventListener("visibilitychange",()=>preview.setHidden(runtimeTab||document.hidden));
    const tabs=document.querySelectorAll("[data-settings-tab]");
    tabs.forEach(b=>b.addEventListener("click",()=>{
      tabs.forEach(item=>{item.setAttribute("aria-selected",String(item===b));item.tabIndex=item===b?0:-1;});
      runtimeTab=b.dataset.settingsTab==="runtime";
      $("brandWorkspace").hidden=runtimeTab; $("runtimeWorkspace").hidden=!runtimeTab;
      preview.setHidden(runtimeTab||document.hidden);
    }));
    tabs.forEach((b,i)=>b.addEventListener("keydown",event=>{
      if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End"].includes(event.key)) return;
      event.preventDefault();
      const index=event.key==="Home"?0:event.key==="End"?tabs.length-1:
        (i+(["ArrowRight","ArrowDown"].includes(event.key)?1:tabs.length-1))%tabs.length;
      tabs[index].focus(); tabs[index].click();
    }));
    function download(blob,name) {
      const url=URL.createObjectURL(blob),a=document.createElement("a");
      a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function configInform(zh,english){configFeedback=[zh,english];renderConfig();}
    function clearDraft(){configDraft=null;++configDraftRevision;configFeedback=null;configInvalid=false;configRowsKey=null;renderConfig();}
    function renderConfig(){
      $("brandConfigCard").setAttribute("aria-busy",String(!!configPending));
      $("brandConfigExport").disabled=!!configPending;
      $("brandConfigApply").disabled=!!configPending||!configDraft||!config.differences(active,configDraft.value).length;
      $("brandConfigUndo").disabled=!!configPending||!display.previous;
      $("brandConfigCancel").disabled=!!configPending;
      $("brandConfigInput").setAttribute("aria-invalid",String(configInvalid));
      $("brandConfigReview").hidden=!configDraft;
      $("brandConfigSystemNote").hidden=!media.matches;
      const message=configFeedback?t(...configFeedback):"";
      if($("brandConfigStatus").textContent!==message)$("brandConfigStatus").textContent=message;
      if(!configDraft)return;
      const rows=config.differences(active,configDraft.value),body=$("brandConfigDiff");
      $("brandConfigNoChanges").hidden=!!rows.length;
      const rowsKey=JSON.stringify([en(),rows]);
      if(rowsKey===configRowsKey)return;
      configRowsKey=rowsKey;body.replaceChildren();
      const labels={variant:t("变体","Variant"),order:t("结构","Structure"),view:t("视角","View"),static:t("静态标识","Static mark"),reduced:t("个人减少动态效果偏好","Personal reduced motion"),pngSize:t("PNG 尺寸","PNG size")};
      const views={front:["正面","Front"],side:["侧面","Side"],top:["顶部","Top"],iso:["三视角","Three-quarter"],oblique:["斜视图","Oblique"],bottom:["底部","Bottom"]};
      const text=(field,value)=>typeof value==="boolean"?t(value?"开启":"关闭",value?"On":"Off"):field==="order"?`${value} × ${value}`:field==="pngSize"?`${value}px`:field==="view"?t(...views[value]):field==="variant"?t(...variantLabels[value]):String(value);
      rows.forEach(row=>{const tr=document.createElement("tr");[labels[row.field],text(row.field,row.before),text(row.field,row.after)].forEach((value,index)=>{const cell=document.createElement(index===0?"th":"td");if(index===0)cell.setAttribute("scope","row");cell.textContent=value;tr.append(cell);});body.append(tr);});
    }
    const configErrors={
      'invalid-json':["JSON 格式错误，当前配置未改变。","Invalid JSON. Current settings are unchanged."],
      'too-large':["配置文件超过 64 KB，当前配置未改变。","Configuration exceeds 64 KB. Current settings are unchanged."],
      'unknown-format':["不是可识别的显示配置文件。","Unrecognized display configuration format."],
      'unknown-version':["配置版本不受支持，当前配置未改变。","Unsupported configuration version. Current settings are unchanged."],
      'unexpected-fields':["包含不允许的字段，当前配置未改变。","Unapproved fields found. Current settings are unchanged."],
      'missing-fields':["配置字段不完整，当前配置未改变。","Required settings are missing. Current settings are unchanged."],
      'invalid-values':["配置值无效，当前配置未改变。","Invalid settings. Current settings are unchanged."]};
    $("brandConfigInput").addEventListener("input",()=>{++fileRevision;$("brandConfigFile").value="";clearDraft();});
    $("brandConfigFile").addEventListener("change",async event=>{
      const file=event.target.files?.[0],revision=++fileRevision;clearDraft();if(!file)return;
      try{
        if(file.size>config.maxBytes)throw new config.ConfigError('too-large');
        const text=await file.text();if(revision!==fileRevision)return;
        clearDraft();
        $("brandConfigInput").value=text;
        configInform("已读取文件，请预览差异。","File read. Review the differences before applying.");
      }catch(error){if(revision===fileRevision)configInform(...(configErrors[error.code]||["无法读取配置文件。","Unable to read the configuration file."]));}
      finally{if(revision===fileRevision)event.target.value="";}
    });
    $("brandConfigPreview").addEventListener("click",()=>{
      const focusPreview=document.activeElement===$("brandConfigPreview");
      clearDraft();
      try{
        configDraft=config.parse($("brandConfigInput").value);
        if(config.differences(active,configDraft.value).length)configInform(configDraft.migrated?"旧版 v1 配置已验证，请检查差异后应用。":"配置已验证，请检查差异后应用。",configDraft.migrated?"Legacy v1 settings validated. Review and apply.":"Settings validated. Review and apply.");
        else configInform("配置与当前一致，无需应用。","These settings already match. No change is needed.");
        if(focusPreview)$("brandConfigReview").focus();
      }
      catch(error){configInvalid=true;configInform(...(configErrors[error.code]||configErrors['invalid-values']));if(focusPreview)$("brandConfigInput").focus();}
    });
    $("brandConfigCancel").addEventListener("click",()=>{
      if(configPending)return;
      const focusCancel=document.activeElement===$("brandConfigCancel");
      clearDraft();configInform("已取消审阅，输入内容保留，当前配置未改变。","Review cancelled. The input is retained and current settings are unchanged.");
      if(focusCancel)$("brandConfigPreview").focus();
    });
    async function configGuard(fn){
      try{return await fn();}
      catch(_){configInform("动效未能复原，当前配置未改变；请先重试动效。","The animation could not be restored. Settings are unchanged; retry the animation first.");return false;}
    }
    $("brandConfigApply").addEventListener("click",()=>configGuard(async()=>{
      if(!configDraft||configPending)return;
      const draft=configDraft,revision=configDraftRevision;
      const focusApply=document.activeElement===$("brandConfigApply");
      if(await display.apply(draft.value)){
        if(revision===configDraftRevision)configDraft=null;
        configInform(display.saved?"配置已应用，可撤销上一次变更。":"配置已应用，但浏览器无法保存；仍可撤销。",display.saved?"Settings applied. The previous change can be undone.":"Settings applied, but browser storage is unavailable. Undo is still available.");
        if(focusApply&&!configDraft&&[document.body,$("brandConfigApply")].includes(document.activeElement))$("brandConfigUndo").focus();
      }
    }));
    $("brandConfigUndo").addEventListener("click",()=>configGuard(async()=>{
      if(configPending)return;
      const focusUndo=document.activeElement===$("brandConfigUndo");
      if(await display.undo()){
        configInform(display.saved?"已撤销上一次配置变更。":"已撤销变更，但浏览器无法保存。",display.saved?"The previous settings change was undone.":"Settings change undone, but browser storage is unavailable.");
        if(focusUndo&&[document.body,$("brandConfigUndo")].includes(document.activeElement))$("brandConfigPreview").focus();
      }
    }));
    $("brandConfigExport").addEventListener("click",()=>guard(()=>{
      if(configPending)return;
      download(new Blob([config.serialize(active)],{type:"application/json"}),"eva-display-config-v1.json");
      configInform("已导出当前有效显示配置。","The current display settings were exported.");
    }));
    function filename(selected,extension) {
      return `eva-${selected.variant}-${selected.order}x${selected.order}-${selected.view}.${extension}`;
    }
    async function rasterize(selected){
      const img=new Image(); img.src=EvaBrandAssets.dataUrl(selected); await img.decode();
      const canvas=document.createElement("canvas"); canvas.width=canvas.height=selected.size;
      canvas.getContext("2d").drawImage(img,0,0);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
      if(!blob)throw new Error("PNG export failed");
      return blob;
    }
    $("brandExportSvg").addEventListener("click",()=>guard(()=>{
      const selected=options();
      download(new Blob([EvaBrandAssets.svg(selected)],{type:"image/svg+xml"}),filename(selected,"svg"));
      inform("已导出当前视角的标准 SVG。","Exported the solved SVG in the selected view.");
    }));
    $("brandExportTokens").addEventListener("click",()=>guard(()=>{
      download(new Blob([JSON.stringify(brand.tokens,null,2)],{type:"application/json"}),"eva-brand-tokens.json");
      inform("已导出品牌 Token。","Brand tokens exported.");
    }));
    $("brandExportPng").addEventListener("click",()=>guard(async()=>{
      if (exporting) return;
      exporting=true; refresh();
      const selected={...options(),size:active.pngSize};
      try {
        const blob=await rasterize(selected);
        download(blob,filename(selected,"png"));
        inform(`已导出 ${selected.size}px 透明 PNG。`,`Exported a ${selected.size}px transparent PNG.`);
      } catch (_) {
        inform("PNG 图像生成或保存失败，请重试导出。","PNG generation or saving failed. Try exporting again.");
      } finally { exporting=false; refresh(); }
    }));
    $("brandExportPackage").addEventListener("click",async()=>{
      if(packaging||configPending)return;
      packaging=true;const selected=delivery.start({...options(),size:active.pngSize});
      const progress=(zh,english)=>{packageFeedback=[zh,english];refresh();};
      progress("正在准备品牌文件…","Preparing brand files…");
      try{
        const components={};
        await Promise.all(EvaBrandPackage.componentNames.map(async name=>{
          const response=await fetch("/static/"+name,{cache:"no-cache"});
          if(!response.ok)throw new Error("Component unavailable");
          components[name]=await response.text();
        }));
        const sourceResponse=await fetch('/static/brand-source.json',{cache:'no-cache'});
        if(!sourceResponse.ok)throw new Error('Brand source record unavailable');
        EvaBrandPackage.assertSource(await sourceResponse.json(),components);
        progress("正在生成透明图像…","Rendering transparent images…");
        const requests=[{name:"selected/eva-selected.png",options:selected},
          ...[16,32,48].map(size=>({name:`icons/favicon-${size}.png`,options:{variant:"favicon",size}}))];
        const pngFiles=[];
        for(const request of requests){
          const blob=await rasterize(request.options);
          pngFiles.push({name:request.name,data:new Uint8Array(await blob.arrayBuffer())});
        }
        const kit=EvaBrandPackage.assemble({selected,components,pngFiles});
        const archive=EvaBrandPackage.zip(kit.files);
        delivery.finish({href:EvaBrandPackage.downloadUrl(archive),name:`eva-brand-${kit.manifest.brandVersion}-components-${kit.manifest.componentVersion}-${selected.order}x${selected.order}-${selected.variant}-${selected.view}-${selected.size}px.zip`});
        renderDelivery({...options(),size:active.pngSize});$("brandPackageSave").click();
        progress(`已生成 ${kit.files.length} 个文件，可离线打开 preview.html。`,`Generated ${kit.files.length} files. Open preview.html offline.`);
      }catch(error){
        delivery.fail();
        const reason=String(error?.message||'');
        if(reason.includes('Unsupported'))progress("无法识别品牌包或规则版本，请更新工作区后重试。","Unsupported brand package or rule version. Update the workspace and retry.");
        else if(reason.includes('sources changed'))progress("品牌来源已变化，请重新生成资产并刷新工作区。","Brand sources changed. Rebuild assets and reload the workspace.");
        else progress("交付包生成失败，请检查来源记录后重试。","Kit export failed. Check the source record and try again.");
      }
      finally{packaging=false;refresh();}
    });
    window.addEventListener("lang-changed",refresh);
    syncControls(); preview.setHidden(document.hidden);
    guard(()=>configure(prefs,{recordUndo:false}));
  }
  return {Preview,KitDelivery,init,preferences,readPreferences,writePreferences,defaults,pngSizes};
});
