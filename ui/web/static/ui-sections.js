// Reading position only: anchors keep their native navigation, history and focus behavior.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else {root.EvaSectionNavigation=api;api.init(root);}
})(globalThis,function(){
  'use strict';
  const mounted=new WeakMap();
  function init(root){
    const document=root.document,nav=document?.querySelector('.studio-section-nav');
    const workspace=document?.getElementById('brandWorkspace');
    if(!nav||!workspace)return null;
    if(mounted.has(nav))return mounted.get(nav);
    const entries=Array.from(nav.querySelectorAll('a[href^="#"]')).map(link=>({link,
      section:document.getElementById(link.getAttribute('href').slice(1))})).filter(entry=>entry.section);
    if(!entries.length)return null;
    const content=document.getElementById('studioContent');
    let frame=null,disposed=false,offset=null;
    function render(){
      frame=null;
      if(disposed||workspace.hidden)return;
      const scroller=content&&content.scrollHeight>content.clientHeight+1?content:document.scrollingElement;
      const bounds=nav.getBoundingClientRect(),navOffset=Math.ceil(bounds.height)+16;
      const padding=scroller===content?parseFloat(root.getComputedStyle?.(content)?.paddingTop)||0:0;
      const nextOffset=navOffset+padding;
      if(nextOffset!==offset){offset=nextOffset;workspace.style.setProperty('--studio-section-offset',offset+'px');}
      let current=entries[0];
      for(const entry of entries){
        if(entry.section.getBoundingClientRect().top<=Math.max(0,bounds.top)+navOffset+2)current=entry;
      }
      if(scroller&&scroller.scrollHeight>scroller.clientHeight+2&&
        scroller.scrollHeight-scroller.clientHeight-scroller.scrollTop<=2){
        // Native anchor focus disambiguates adjacent sections that both fit at the page end.
        current=entries.find(entry=>entry.section===document.activeElement)||current;
      }
      for(const entry of entries){
        if(entry===current){if(entry.link.getAttribute('aria-current')!=='location')entry.link.setAttribute('aria-current','location');}
        else if(entry.link.getAttribute('aria-current'))entry.link.removeAttribute('aria-current');
      }
    }
    function schedule(){
      if(!disposed&&frame===null)frame=root.requestAnimationFrame(render);
    }
    const resize=root.ResizeObserver?new root.ResizeObserver(schedule):null;
    resize?.observe(nav);if(content)resize?.observe(content);
    const visibility=root.MutationObserver?new root.MutationObserver(schedule):null;
    visibility?.observe(workspace,{attributes:true,attributeFilter:['hidden']});
    document.addEventListener('scroll',schedule,{capture:true,passive:true});
    root.addEventListener('resize',schedule);root.addEventListener('hashchange',schedule);
    root.addEventListener('lang-changed',schedule);
    const controller={render,dispose(){
      disposed=true;if(frame!==null)root.cancelAnimationFrame(frame);
      resize?.disconnect();visibility?.disconnect();
      document.removeEventListener('scroll',schedule,true);
      for(const event of ['resize','hashchange','lang-changed'])root.removeEventListener(event,schedule);
      workspace.style.removeProperty('--studio-section-offset');
      for(const entry of entries)entry.link.removeAttribute('aria-current');
      mounted.delete(nav);
    }};
    mounted.set(nav,controller);render();return controller;
  }
  return Object.freeze({init});
});
