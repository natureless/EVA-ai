const {test}=require('node:test');
const assert=require('node:assert/strict');
const Reading=require('../../ui/web/static/ui-reading.js');
function harness(){
  const events=new Map(),listeners=new Map();let resize,disconnected=false,language='zh';
  const document={activeElement:null};
  const last={focus(options){document.activeElement=this;this.options=options;}};
  const container={scrollHeight:1200,clientHeight:400,scrollTop:800,
    addEventListener(k,v){events.set(k,v);},removeEventListener(k){events.delete(k);},querySelector(){return last;}};
  const button={hidden:true,events:new Map(),addEventListener(k,v){this.events.set(k,v);},removeEventListener(k){this.events.delete(k);}};
  document.getElementById=id=>({chatMessages:container,chatLatest:button})[id];
  const root={document,I18N:{lang:()=>language},addEventListener(k,v){listeners.set(k,v);},removeEventListener(k){listeners.delete(k);},
    ResizeObserver:class{constructor(fn){resize=fn;}observe(){}disconnect(){disconnected=true;}}};
  return {root,document,container,button,last,events,listeners,resize:()=>resize(),disposed:()=>disconnected,english(){language='en';listeners.get('lang-changed')();}};
}
test('streaming preserves the reader position and an explicit jump restores following and focus',()=>{
  const h=harness(),controller=Reading.init(h.root);
  h.container.scrollTop=200;h.events.get('scroll')();
  assert.equal(h.button.hidden,false);
  h.container.scrollHeight=2000;controller.update({follow:false});h.resize();
  assert.equal(h.container.scrollTop,200,'new text and resizing must not pull a reader away');
  h.english();assert.equal(h.button.textContent,'↓ Latest message');
  h.document.activeElement=h.button;h.button.events.get('click')();
  assert.equal(h.button.hidden,true);assert.equal(h.document.activeElement,h.last);
  assert.deepEqual(h.last.options,{preventScroll:true});
  h.container.scrollHeight=2400;h.resize();assert.equal(h.container.scrollTop,2400);
});
test('reading controls mount once and release their resize, language and scrolling listeners',()=>{
  const h=harness(),controller=Reading.init(h.root);
  assert.equal(Reading.init(h.root),controller);
  assert.equal(h.events.size,1);assert.equal(h.listeners.size,1);
  controller.dispose();assert.equal(h.events.size,0);assert.equal(h.listeners.size,0);
  assert.equal(h.button.events.size,0);assert.equal(h.disposed(),true);
  assert.notEqual(Reading.init(h.root),controller);
});
