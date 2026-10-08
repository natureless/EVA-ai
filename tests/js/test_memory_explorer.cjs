const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const staticDir = path.join(__dirname, '../../ui/web/static');
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function harness() {
  const nodes = new Map(), requests = [], timers = new Map(), events = {};
  function node(id='') {
    return {id, innerHTML:'', textContent:'', value:'', hidden:false, dataset:{}, style:{}, attrs:{}, handlers:{}, children:[],
      setAttribute(k,v) {this.attrs[k]=String(v);}, removeAttribute(k) {delete this.attrs[k];},
      addEventListener(k,v) {(this.handlers[k] ||= []).push(v);},
      append(...items) {this.children.push(...items); for(const item of items) if(item.id) nodes.set(item.id,item);},
      contains() {return false;}, querySelectorAll() {return [];}, focus() {}};
  }
  for(const id of ['memTiers','memTierStatus','memTierRetry','memResultsTitle','memResultsCount','memEntries','memResultsStatus','memResultsRetry','memSearch','memSearchBtn']) nodes.set(id,node(id));
  const document = {getElementById:id=>nodes.get(id)||null, createElement:()=>node(), body:node(), activeElement:null};
  let timerId=0;
  const context = {document, I18N:{_lang:'en'}, Nav:{init() {}}, AbortController,
    setTimeout(fn) {const id=++timerId;timers.set(id,fn);return id;}, clearTimeout(id) {timers.delete(id);},
    setInterval(fn) {const id=++timerId;timers.set(id,fn);return id;},
    addEventListener(k,fn) {(events[k] ||= []).push(fn);}, location:{reload() {}},
    fetch(url,options) {return new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));}};
  context.window=context;
  vm.runInNewContext(fs.readFileSync(path.join(staticDir,'http.js'),'utf8'),context);
  vm.runInNewContext(fs.readFileSync(path.join(staticDir,'memory_explorer.js'),'utf8')+'\nglobalThis.explorer=MemoryExplorer;',context);
  return {context, nodes, requests, timers, events, explorer:context.explorer,
    respond(index,data,status=200) {requests[index].resolve(Response.json(data,{status}));},
    click(id) {return nodes.get(id).handlers.click?.[0]();}};
}
const tiers = {tiers:['S2','S3'].map(name=>({name,label:name,entries:1,max:10,ttl:'1d',storage:'sqlite'}))};
async function ready() {const h=harness();h.respond(0,tiers);await flush();return h;}

test('late tier result and late failure cannot replace the newest selection',async()=>{
  const h=await ready();
  const older=h.explorer._selectTier('S2');const newer=h.explorer._selectTier('S3');
  h.respond(2,{total:1,entries:[{content:'new-selection'}]});await newer;
  h.respond(1,{total:1,entries:[{content:'old-selection'}]});await older;
  assert.match(h.nodes.get('memEntries').innerHTML,/new-selection/);
  assert.doesNotMatch(h.nodes.get('memEntries').innerHTML,/old-selection/);
  const failing=h.explorer._selectTier('S2');const current=h.explorer._selectTier('S3');
  h.respond(4,{total:1,entries:[{content:'current-again'}]});await current;
  h.requests[3].reject(new Error('<img src=x onerror=oops>'));await failing;
  assert.match(h.nodes.get('memEntries').innerHTML,/current-again/);
  assert.equal(h.nodes.get('memResultsStatus').textContent,'');
});

test('search and tier browsing share one result ownership boundary',async()=>{
  const h=await ready();h.nodes.get('memSearch').value='draft query';h.click('memSearchBtn');
  const browse=h.explorer._selectTier('S2');
  h.respond(2,{total:1,entries:[{content:'chosen-tier'}]});await browse;
  h.respond(1,{total:1,results:{S3:[{content:'old-search'}]}});await flush();
  assert.match(h.nodes.get('memEntries').innerHTML,/chosen-tier/);
  assert.doesNotMatch(h.nodes.get('memEntries').innerHTML,/old-search/);
  assert.equal(h.nodes.get('memSearch').value,'draft query');
});

test('HTTP failure is recoverable and never presented as successful empty search',async()=>{
  const h=await ready();h.nodes.get('memSearch').value='needle';h.click('memSearchBtn');
  h.respond(1,{detail:'unavailable'},503);await flush();
  assert.match(h.nodes.get('memResultsStatus').textContent,/503/);
  assert.equal(h.nodes.get('memResultsRetry').hidden,false);
  assert.doesNotMatch(h.nodes.get('memEntries').innerHTML,/No results found/);
  h.nodes.get('memSearch').value='unsent changed draft';h.click('memResultsRetry');
  assert.equal(JSON.parse(h.requests[2].options.body).query,'needle');
  h.respond(2,{total:0,results:{}});await flush();
  assert.match(h.nodes.get('memEntries').innerHTML,/No results found/);
  assert.equal(h.nodes.get('memResultsRetry').hidden,true);
  assert.equal(h.nodes.get('memSearch').value,'unsent changed draft');
});

test('failed overview refresh preserves known cards with explicit stale notice; retry recovers',async()=>{
  const h=await ready();const cards=h.nodes.get('memTiers').innerHTML;
  const update=h.explorer._loadTiers();h.respond(1,{detail:'unavailable'},503);await update;
  assert.equal(h.nodes.get('memTiers').innerHTML,cards);
  assert.match(h.nodes.get('memTierStatus').textContent,/previous|last/i);
  assert.equal(h.nodes.get('memTierRetry').hidden,false);
  h.click('memTierRetry');h.respond(2,tiers);await flush();
  assert.equal(h.nodes.get('memTierStatus').textContent,'');
  assert.equal(h.nodes.get('memTierRetry').hidden,true);
});

test('malformed successful data and initial failures are distinct from valid empty states',async()=>{
  const h=harness();h.respond(0,{detail:'wrong-shape'});await flush();
  assert.match(h.nodes.get('memTierStatus').textContent,/read|data/i);
  const pending=h.explorer._selectTier('S2');h.respond(1,{detail:'wrong-shape'});await pending;
  assert.match(h.nodes.get('memResultsStatus').textContent,/read|data/i);
  assert.doesNotMatch(h.nodes.get('memEntries').innerHTML,/No entries/);
  const empty=h.explorer._selectTier('S2');h.respond(2,{total:0,entries:[]});await empty;
  assert.match(h.nodes.get('memEntries').innerHTML,/No entries/);
  assert.equal(h.nodes.get('memEntries').attrs['aria-busy'],'false');
});

test('login HTML and unauthorized responses keep the query and stop subsequent requests',async()=>{
  for(const response of [new Response('<html>login</html>',{headers:{'content-type':'text/html'}}),Response.json({detail:'Unauthorized'},{status:401})]) {
    const h=await ready();h.nodes.get('memSearch').value='private draft';h.click('memSearchBtn');
    h.requests[1].resolve(response);await flush();
    assert.equal(h.context.EvaHttp.loginRequired,true);
    assert.equal(h.nodes.get('memSearch').value,'private draft');
    assert.equal(h.nodes.get('memResultsRetry').hidden,true);
    assert.equal(h.requests.length,2);
    h.click('memSearchBtn');await flush();assert.equal(h.requests.length,2);
  }
});

test('repeated initialization and slow overview polling do not duplicate listeners or requests',async()=>{
  const h=await ready();const listeners=h.nodes.get('memSearchBtn').handlers.click.length;
  h.explorer.init();assert.equal(h.nodes.get('memSearchBtn').handlers.click.length,listeners);
  const first=h.explorer._loadTiers();const second=h.explorer._loadTiers();
  assert.equal(h.requests.length,2);h.respond(1,tiers);await Promise.all([first,second]);
});

test('language changes rerender cached results without fetching or clearing drafts',async()=>{
  const h=await ready();const pending=h.explorer._selectTier('S3');h.respond(1,{total:1,entries:[{content:'saved content'}]});await pending;
  h.nodes.get('memSearch').value='pending draft';h.context.I18N._lang='zh';
  for(const fn of h.events['lang-changed']||[]) fn();
  assert.match(h.nodes.get('memResultsTitle').textContent,/浏览/);
  assert.match(h.nodes.get('memEntries').innerHTML,/saved content/);
  assert.equal(h.requests.length,2);assert.equal(h.nodes.get('memSearch').value,'pending draft');
});

test('request timeout exposes retry and aborts the original read',async()=>{
  const h=await ready();const pending=h.explorer._selectTier('S2');
  const request=h.requests[1];let timer;
  // The request timeout is registered after the overview poll timer.
  for(const fn of h.timers.values()) timer=fn;
  request.options.signal.addEventListener('abort',()=>request.reject(new DOMException('Aborted','AbortError')));
  timer();await pending;
  assert.equal(request.options.signal.aborted,true);
  assert.match(h.nodes.get('memResultsStatus').textContent,/timed out/i);
  assert.equal(h.nodes.get('memResultsRetry').hidden,false);
});
