const {test}=require('node:test');
const assert=require('node:assert/strict');
const Sections=require('../../ui/web/static/ui-sections.js');

function harness(){
  const events=new Map(),documentEvents=new Map(),frames=new Map(),observers=[];
  const values=new Map(),workspace={hidden:false,style:{setProperty:(k,v)=>values.set(k,v),removeProperty:k=>values.delete(k)}};
  const ids=['appearance','preview','config','assets','delivery'];
  const links=ids.map(id=>({attrs:{href:'#'+id},getAttribute(k){return this.attrs[k];},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}}));
  const positions=[120,300,600,900,1200];
  const sections=Object.fromEntries(ids.map((id,i)=>[id,{getBoundingClientRect:()=>({top:positions[i]})}]));
  const bounds={top:20,height:50,bottom:70};
  const nav={querySelectorAll:()=>links,getBoundingClientRect:()=>bounds};
  const content={clientHeight:700,scrollHeight:1600,scrollTop:0};
  const page={clientHeight:900,scrollHeight:900,scrollTop:0};
  const document={querySelector:()=>nav,getElementById:id=>({brandWorkspace:workspace,studioContent:content,...sections})[id],scrollingElement:page,
    activeElement:{value:'unsaved config'},addEventListener:(k,v)=>documentEvents.set(k,v),removeEventListener:k=>documentEvents.delete(k)};
  const root={document,addEventListener:(k,v)=>events.set(k,v),removeEventListener:k=>events.delete(k),
    requestAnimationFrame:fn=>{const id=frames.size+1;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
    ResizeObserver:class{constructor(fn){this.callback=fn;this.targets=[];observers.push(this);}observe(el){this.targets.push(el);}disconnect(){this.disconnected=true;}},
    MutationObserver:class{constructor(fn){this.callback=fn;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}}};
  return {root,document,events,documentEvents,frames,values,workspace,content,page,positions,bounds,links,sections,observers,
    current:()=>links.filter(link=>link.attrs['aria-current']).map(link=>link.attrs.href),
    flush(){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());}};
}

test('scrolling updates the current section without changing the focused edit or anchor behavior',()=>{
  const h=harness(),edit=h.document.activeElement;Sections.init(h.root);
  assert.deepEqual(h.current(),['#appearance']);
  h.positions.splice(0,3,-500,-300,84);h.documentEvents.get('scroll')();h.flush();
  assert.deepEqual(h.current(),['#config']);assert.equal(h.document.activeElement,edit);
  assert.equal(edit.value,'unsaved config');assert.equal(h.links[2].attrs.href,'#config');
});

test('wrapped navigation changes the offset and includes the fractional native anchor landing',()=>{
  const h=harness();Sections.init(h.root);assert.equal(h.values.get('--studio-section-offset'),'66px');
  h.bounds.height=90.5;h.bounds.bottom=110.5;h.positions.splice(0,3,-500,-200,128);
  h.observers[0].callback();h.flush();
  assert.equal(h.values.get('--studio-section-offset'),'107px');assert.deepEqual(h.current(),['#config']);
});

test('desktop anchor spacing includes the padded scrolling container without doubling the reading threshold',()=>{
  const h=harness();h.root.getComputedStyle=()=>({paddingTop:'38px'});
  h.bounds.top=38;h.bounds.height=53;h.bounds.bottom=91;
  h.positions.splice(0,3,-500,-200,107);Sections.init(h.root);
  assert.equal(h.values.get('--studio-section-offset'),'107px');assert.deepEqual(h.current(),['#config']);
});

test('the last section remains selected when the scroll container cannot align it at the top',()=>{
  const h=harness();Sections.init(h.root);h.content.scrollTop=900;h.document.activeElement=h.sections.delivery;
  h.documentEvents.get('scroll')();h.flush();assert.deepEqual(h.current(),['#delivery']);
  // At phone sizes the page becomes the scroller instead of the settings content.
  h.content.scrollHeight=h.content.clientHeight=1600;h.page.scrollHeight=2500;h.page.scrollTop=1600;
  h.events.get('resize')();h.flush();assert.deepEqual(h.current(),['#delivery']);
});

test('page-end reading does not force the last section or steal focus from an edit',()=>{
  const h=harness(),edit=h.document.activeElement;h.positions.splice(0,5,-800,-500,-300,80,350);
  h.content.scrollTop=900;Sections.init(h.root);
  assert.deepEqual(h.current(),['#assets']);assert.equal(h.document.activeElement,edit);
  h.document.activeElement=h.sections.assets;h.events.get('hashchange')();h.flush();
  assert.deepEqual(h.current(),['#assets']);
});

test('hidden runtime panels leave reading state intact and restore tracking on return',()=>{
  const h=harness();Sections.init(h.root);h.workspace.hidden=true;h.positions.splice(0,3,-300,-200,70);
  h.observers[1].callback();h.flush();assert.deepEqual(h.current(),['#appearance']);
  h.workspace.hidden=false;h.observers[1].callback();h.flush();assert.deepEqual(h.current(),['#config']);
});

test('repeated mounting shares listeners and cleanup cancels queued work and observers',()=>{
  const h=harness(),first=Sections.init(h.root);assert.equal(Sections.init(h.root),first);
  h.documentEvents.get('scroll')();h.events.get('resize')();assert.equal(h.frames.size,1);
  first.dispose();assert.equal(h.frames.size,0);assert.equal(h.events.size,0);assert.equal(h.documentEvents.size,0);
  assert.ok(h.observers.every(observer=>observer.disconnected));assert.deepEqual(h.current(),[]);
  assert.equal(h.values.has('--studio-section-offset'),false);assert.notEqual(Sections.init(h.root),first);
});
