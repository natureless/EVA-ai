const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {test} = require('node:test');
const Workspace = require('../../ui/web/static/ui-workspace.js');

function node() {
  return {attrs:{}, dataset:{}, events:new Map(), hidden:false,
    setAttribute(key,value) {this.attrs[key]=value;}, getAttribute(key) {return this.attrs[key];},
    addEventListener(key,fn) {this.events.set(key,fn);}, removeEventListener(key) {this.events.delete(key);},
    contains(active) {return this.child === active;}, querySelector() {return this.label ||= node();}};
}
function disclosureHarness(compact=true) {
  const ids = Object.fromEntries(['cubePanel','cubeDisclosure','cubeDetails','cubeOrderControls','cubeStage'].map(id=>[id,node()]));
  const document = {getElementById:id=>ids[id], activeElement:null};
  Object.values(ids).forEach(el=>el.focus=()=>{document.activeElement=el;});
  const media=node(); media.matches=compact;
  let lang='zh';
  const root={document,matchMedia:()=>media,I18N:{t:key=>lang==='en'
    ? (key==='ui.cubeCollapse'?'Collapse cube demo':'Expand cube demo') : (key==='ui.cubeCollapse'?'收起魔方演示':'展开魔方演示')},
    events:new Map(),addEventListener(key,fn){this.events.set(key,fn);},removeEventListener(key){this.events.delete(key);}};
  return {ids,root,document,media,
    resize(matches){media.matches=matches;media.events.get('change')();},
    click(){ids.cubeDisclosure.events.get('click')();},
    english(){lang='en';root.events.get('lang-changed')();}};
}

test('disclosure retains expansion and focus across desktop and compact breakpoints',()=>{
  const h=disclosureHarness(false);
  Workspace.init(h.root);
  const hiddenControl=node(); h.ids.cubeDetails.child=hiddenControl; h.document.activeElement=hiddenControl;
  h.resize(true);
  assert.equal(h.ids.cubeDetails.hidden,true);
  assert.equal(h.ids.cubeOrderControls.hidden,true);
  assert.equal(h.document.activeElement,h.ids.cubeDisclosure);
  assert.equal(h.ids.cubeDisclosure.attrs['aria-expanded'],'false');
  h.click();
  assert.equal(h.ids.cubeDetails.hidden,false);
  assert.equal(h.ids.cubeDisclosure.attrs['aria-expanded'],'true');
  h.english();
  assert.equal(h.ids.cubeDisclosure.attrs['aria-label'],'Collapse cube demo');
  h.document.activeElement=h.ids.cubeDisclosure;
  h.resize(false);
  assert.equal(h.ids.cubeDisclosure.hidden,false);
  assert.equal(h.document.activeElement,h.ids.cubeDisclosure);
  h.resize(true);
  assert.equal(h.ids.cubeDetails.hidden,false,'explicit expansion is retained across resizing');
});

test('desktop controls are optional and folding preserves the draft and cube instance',()=>{
  const h=disclosureHarness(false);
  const draft={value:'An unfinished idea'};
  h.ids.chatInput=draft;
  const stage=h.ids.cubeStage;
  Workspace.init(h.root);
  assert.equal(h.ids.cubeDetails.hidden,true);
  assert.equal(h.ids.cubeOrderControls.hidden,true);
  assert.equal(h.ids.cubeDisclosure.hidden,false);
  h.click();
  const control=node();h.ids.cubeDetails.child=control;h.document.activeElement=control;
  h.click();
  assert.equal(h.document.activeElement,h.ids.cubeDisclosure);
  assert.equal(h.ids.cubeDetails.hidden,true);
  assert.equal(h.ids.cubeStage,stage);
  assert.equal(draft.value,'An unfinished idea');
});

test('repeated layout mounting reuses listeners and disposal releases the registered handlers',()=>{
  const h=disclosureHarness();
  const first=Workspace.init(h.root);
  assert.equal(Workspace.init(h.root),first);
  assert.equal(h.root.events.size,1);
  assert.equal(h.media.events.size,1);
  first.dispose();
  assert.equal(h.root.events.size,0);
  assert.equal(h.media.events.size,0);
  assert.equal(h.ids.cubeDisclosure.events.size,0);
  assert.equal(h.ids.cubePanel.events.size,0);
  assert.notEqual(Workspace.init(h.root),first);
});

test('Escape folds focused demo controls but does not intercept a draft or composition',()=>{
  const h=disclosureHarness(false);Workspace.init(h.root);h.click();
  const control=node();h.ids.cubeDetails.child=control;
  const key=h.ids.cubePanel.events.get('keydown');let prevented=0;
  const event={key:'Escape',preventDefault(){prevented++;}};
  h.document.activeElement={value:'draft'};key(event);
  assert.equal(h.ids.cubeDetails.hidden,false);assert.equal(prevented,0);
  h.document.activeElement=control;key({...event,isComposing:true});
  assert.equal(h.ids.cubeDetails.hidden,false);key(event);
  assert.equal(h.ids.cubeDetails.hidden,true);assert.equal(prevented,1);
  assert.equal(h.document.activeElement,h.ids.cubeDisclosure);
});

test('settings tab orientation follows layout without changing the selected category',()=>{
  const tabs=node(); tabs.attrs['data-selected']='brand';
  const media=node(); media.matches=false;
  const root={document:{getElementById:()=>null,querySelector:()=>tabs},matchMedia:()=>media};
  const controller=Workspace.init(root);
  assert.equal(tabs.attrs['aria-orientation'],'vertical');
  media.matches=true; media.events.get('change')();
  assert.equal(tabs.attrs['aria-orientation'],'horizontal');
  assert.equal(tabs.attrs['data-selected'],'brand');
  controller.dispose(); assert.equal(media.events.size,0);
});

test('language labels and alt text switch both ways even when browser storage is unavailable',()=>{
  const group=node(), image=node(), button=node();
  group.attrs['data-i18n-aria-label']='ui.views'; image.attrs['data-i18n-alt']='ui.logoAlt';
  button.attrs['data-i18n']='ui.skipChat';
  const document={documentElement:{},addEventListener(){},querySelectorAll:selector=>({
    '[data-i18n-aria-label]':[group], '[data-i18n-alt]':[image], '[data-i18n]':[button]})[selector]||[]};
  const context={document,localStorage:{getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../ui/web/static/i18n.js'),'utf8')+'\nthis.i18n=I18N;',context);
  context.i18n.setLang('en'); context.i18n.applyDOM();
  assert.equal(group.attrs['aria-label'],'Cube views');
  assert.equal(image.attrs.alt,'EVA modular cube logo');
  assert.equal(button.textContent,'Skip to message input');
  context.i18n.toggle(); context.i18n.applyDOM();
  assert.equal(group.attrs['aria-label'],'魔方视角');
  assert.equal(document.documentElement.lang,'zh-CN');
});

test('unavailable theme storage does not stop current-page theme controls',()=>{
  const button=node(); const element=node();
  const context={document:{getElementById:()=>button,documentElement:element,
    querySelectorAll:selector=>selector==='[data-theme-status]'?[]:[button]},
    localStorage:{getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../ui/web/static/ui-theme.js'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../../ui/web/static/nav.js'),'utf8')+'\nthis.nav=Nav;',context);
  context.nav._bindTheme(); assert.equal(element.attrs['data-theme'],'light');
  button.events.get('click')(); assert.equal(element.attrs['data-theme'],'dark');
  button.events.get('click')(); assert.equal(element.attrs['data-theme'],'light');
});

test('browser disclosure uses the shipped lexical I18N binding after a language change',()=>{
  const h=disclosureHarness();
  delete h.root.I18N;
  h.document.documentElement={}; h.document.addEventListener=()=>{};
  const context={...h.root,localStorage:{getItem:()=>null,setItem(){}},
    addEventListener:(key,fn)=>h.root.events.set(key,fn),removeEventListener:(key)=>h.root.events.delete(key)};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../ui/web/static/i18n.js'),'utf8')+'\n'+
    fs.readFileSync(path.join(__dirname,'../../ui/web/static/ui-workspace.js'),'utf8')+'\nI18N.setLang("en");',context);
  h.root.events.get('lang-changed')();
  assert.equal(h.ids.cubeDisclosure.attrs['aria-label'],'Expand cube demo');
});
