// One appearance preference for every EVA page. Loaded in <head> before paint.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.EvaTheme=api.create(root);
})(globalThis,function(){
  'use strict';
  const choices=Object.freeze(['light','dark','system']);
  const normalize=value=>choices.includes(value)?value:'light';
  function create(root){
    const doc=root.document,element=doc.documentElement;
    const media=root.matchMedia?.('(prefers-color-scheme: dark)');
    let preference='light',saved=true,disposed=false;
    try{preference=normalize(root.localStorage.getItem('eva-theme'));}catch(_){saved=false;}
    const controls=new Map();
    const effective=()=>preference==='system'?(media?.matches?'dark':'light'):preference;
    const english=()=>element.lang==='en';
    function render(notify=true){
      const theme=effective();element.setAttribute('data-theme',theme);element.setAttribute('data-theme-preference',preference);
      if(element.style)element.style.colorScheme=theme;
      root.EvaBrand?.applyCss(element,theme);
      for(const control of controls.keys()){
        if(control.matches?.('select[data-theme-preference]'))control.value=preference;
        else{const label=english()?(theme==='dark'?'Switch to light theme':'Switch to dark theme'):(theme==='dark'?'切换为浅色主题':'切换为深色主题');
          control.setAttribute('aria-label',label);control.setAttribute('title',label);control.setAttribute('aria-pressed',String(theme==='dark'));}
      }
      for(const status of doc.querySelectorAll?.('[data-theme-status]')||[])status.textContent=saved?'':(english()?'Theme applied. Browser storage is unavailable.':'主题已应用，但浏览器无法保存。');
      if(notify&&root.CustomEvent)root.dispatchEvent(new root.CustomEvent('theme-changed',{detail:{preference,theme,saved}}));
    }
    function set(value){if(!choices.includes(value))throw new RangeError('Unknown appearance preference');preference=value;
      try{root.localStorage.setItem('eva-theme',value);saved=true;}catch(_){saved=false;}render();}
    function bind(){
      if(disposed)return;
      for(const control of doc.querySelectorAll?.('#themeBtn,[data-theme-toggle],select[data-theme-preference]')||[]){
        if(controls.has(control))continue;
        const select=control.matches?.('select[data-theme-preference]'),type=select?'change':'click';
        const handler=()=>set(select?control.value:(effective()==='dark'?'light':'dark'));
        control.addEventListener(type,handler);controls.set(control,{type,handler});
      }
      render(false);
    }
    const system=()=>{if(preference==='system')render();};
    const storage=event=>{if(event.key!=='eva-theme'&&event.key!==null)return;preference=normalize(event.newValue);saved=true;render();};
    const language=()=>render(false);
    root.addEventListener?.('storage',storage);root.addEventListener?.('lang-changed',language);media?.addEventListener('change',system);
    doc.addEventListener?.('DOMContentLoaded',bind,{once:true});render(false);
    if(doc.readyState!=='loading')bind();
    return Object.freeze({choices,set,bind,get:()=>({preference,theme:effective(),saved}),
      dispose(){if(disposed)return;disposed=true;for(const [control,{type,handler}] of controls)control.removeEventListener(type,handler);controls.clear();
        root.removeEventListener?.('storage',storage);root.removeEventListener?.('lang-changed',language);media?.removeEventListener('change',system);doc.removeEventListener?.('DOMContentLoaded',bind);}});
  }
  return Object.freeze({create,normalize,choices});
});
