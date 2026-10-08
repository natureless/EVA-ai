const {test}=require('node:test');
const assert=require('node:assert/strict');
const Theme=require('../../ui/web/static/ui-theme.js');
function harness({saved='light',systemDark=false,blocked=false}={}){
  const events=new Map(),writes=[],broadcasts=[];
  const node=tag=>({tag,attrs:{},listeners:new Map(),value:'',style:{},lang:'zh-CN',setAttribute(k,v){this.attrs[k]=v;},
    matches(selector){return selector==='select[data-theme-preference]'&&tag==='select';},
    addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k){this.listeners.delete(k);}});
  const html=node('html'),button=node('button'),select=node('select'),status=node('p'),media=node('media');media.matches=systemDark;
  const doc={documentElement:html,readyState:'loading',listeners:new Map(),
    querySelectorAll(selector){if(selector==='[data-theme-status]')return [status];return selector.includes('select[data-theme-preference]')?[button,select]:[html,button,select];},
    addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k){this.listeners.delete(k);}};
  const root={document:doc,matchMedia:()=>media,localStorage:{getItem(){if(blocked)throw Error();return saved;},setItem(k,v){if(blocked)throw Error();writes.push([k,v]);}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    addEventListener(k,v){events.set(k,v);},removeEventListener(k){events.delete(k);},dispatchEvent(e){broadcasts.push(e);}};
  return {root,doc,html,button,select,status,media,events,writes,broadcasts};
}
test('saved appearance is applied before controls exist and repeated binding never duplicates the toggle',()=>{
  const h=harness({saved:'dark'}),theme=Theme.create(h.root);
  assert.equal(h.html.attrs['data-theme'],'dark');assert.equal(h.html.style.colorScheme,'dark');assert.equal(h.writes.length,0);
  theme.bind();theme.bind();assert.equal(h.html.listeners.size,0);assert.equal(h.button.listeners.size,1);
  h.button.listeners.get('click')();assert.deepEqual(theme.get(),{preference:'light',theme:'light',saved:true});assert.equal(h.writes.length,1);
  theme.dispose();assert.equal(h.button.listeners.size,0);assert.equal(h.media.listeners.size,0);assert.equal(h.events.size,0);
});
test('system appearance follows OS changes while explicit user choices remain stable',()=>{
  const h=harness({saved:'system',systemDark:true}),theme=Theme.create(h.root);theme.bind();
  assert.equal(theme.get().theme,'dark');assert.equal(h.select.value,'system');h.media.matches=false;h.media.listeners.get('change')();
  assert.equal(theme.get().theme,'light');theme.set('dark');h.media.listeners.get('change')();assert.equal(theme.get().theme,'dark');
  const before=theme.get();assert.throws(()=>theme.set('purple'),RangeError);assert.deepEqual(theme.get(),before);
});
test('storage events synchronize other pages and unrelated preferences never change the current theme',()=>{
  const h=harness(),theme=Theme.create(h.root);theme.bind();h.events.get('storage')({key:'eva-theme',newValue:'dark'});
  assert.equal(h.html.attrs['data-theme'],'dark');assert.equal(h.select.value,'dark');
  h.events.get('storage')({key:'eva-lang',newValue:'en'});assert.equal(theme.get().theme,'dark');
  h.events.get('storage')({key:'eva-theme',newValue:'unknown'});assert.equal(theme.get().theme,'light');assert.equal(h.writes.length,0);
});
test('unavailable storage still applies current-page appearance and reports the unsaved preference',()=>{
  const h=harness({blocked:true}),theme=Theme.create(h.root);theme.bind();theme.set('dark');
  assert.equal(h.html.attrs['data-theme'],'dark');assert.equal(theme.get().saved,false);assert.match(h.status.textContent,/无法保存/);
  h.html.lang='en';h.events.get('lang-changed')();assert.match(h.status.textContent,/storage is unavailable/);assert.equal(h.button.attrs['aria-label'],'Switch to light theme');
});
