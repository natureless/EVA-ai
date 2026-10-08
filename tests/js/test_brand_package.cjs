const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const zlib=require('node:zlib');
const kit=require('../../ui/web/static/brand-package.js');
const assets=require('../../ui/web/static/brand-assets.js');
const brand=require('../../ui/web/static/logo-tokens.js');
const source=path.resolve(__dirname,'../../ui/web/static');
const components=Object.fromEntries(kit.componentNames.map(name=>[name,fs.readFileSync(path.join(source,name),'utf8')]));

// Independent ZIP reader exercises central offsets, UTF-8 names and stored binary content.
function unzip(content){
  const data=Buffer.from(content),end=data.length-22;
  assert.equal(data.readUInt32LE(end),0x06054b50);
  const count=data.readUInt16LE(end+10),start=data.readUInt32LE(end+16),files=new Map();
  let at=start;
  for(let i=0;i<count;i++){
    assert.equal(data.readUInt32LE(at),0x02014b50);
    assert.equal(data.readUInt16LE(at+8),0x800);
    assert.equal(data.readUInt16LE(at+10),0);
    const length=data.readUInt16LE(at+28),name=data.subarray(at+46,at+46+length).toString('utf8');
    const local=data.readUInt32LE(at+42),size=data.readUInt32LE(at+24);
    assert.equal(data.readUInt32LE(local),0x04034b50);
    assert.equal(data.readUInt32LE(local+14),data.readUInt32LE(at+16));
    const localLength=data.readUInt16LE(local+26),body=local+30+localLength;
    assert.equal(data.subarray(local+30,body).toString('utf8'),name);
    assert.equal(data.readUInt32LE(local+22),size);
    files.set(name,data.subarray(body,body+size));
    at+=46+length;
  }
  assert.equal(at,end);assert.equal(at-start,data.readUInt32LE(end+12));
  return files;
}
function chunk(type,data){
  const header=Buffer.alloc(4);header.writeUInt32BE(data.length);
  const body=Buffer.concat([Buffer.from(type),data]),tail=Buffer.alloc(4);tail.writeUInt32BE(kit.crc32(body));
  return Buffer.concat([header,body,tail]);
}
function png(size){
  const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(size,0);ihdr.writeUInt32BE(size,4);ihdr[8]=8;ihdr[9]=6;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),
    chunk('IDAT',zlib.deflateSync(Buffer.alloc((size*4+1)*size))),chunk('IEND',Buffer.alloc(0))]);
}
function pngFiles(size=256){return [{name:'selected/eva-selected.png',data:png(size)},
  ...[16,32,48].map(value=>({name:`icons/favicon-${value}.png`,data:png(value)}))];}

test('ZIP round-trips UTF-8 text and binary payloads, with standard CRC32',()=>{
  assert.equal(kit.crc32('123456789'),0xcbf43926);assert.equal(kit.crc32(''),0);
  const files=[{name:'规范/使用.md',data:'EVA · 品牌\n'},{name:'icons/image.bin',data:new Uint8Array([0,255,128,13,10])}];
  const decoded=unzip(kit.zip(files));
  assert.equal(decoded.get(files[0].name).toString('utf8'),files[0].data);
  assert.deepEqual(decoded.get(files[1].name),Buffer.from(files[1].data));
  assert.deepEqual(kit.zip(files),kit.zip(files));assert.equal(unzip(kit.zip([])).size,0);
  const archive=kit.zip(files),url=kit.downloadUrl(archive);
  assert.ok(url.startsWith('data:application/zip;base64,'));
  assert.deepEqual(Buffer.from(url.split(',')[1],'base64'),Buffer.from(archive));
});

test('ZIP rejects ambiguous, absolute or duplicate paths before producing a download',()=>{
  for(const name of ['../mark.svg','/mark.svg','C:/mark.svg','icons\\mark.svg','icons/../mark.svg','icons//mark.svg','icons/./mark.svg','mark\u0000.svg'])assert.throws(()=>kit.zip([{name,data:'mark'}]),RangeError);
  assert.throws(()=>kit.zip([{name:'mark.svg',data:'a'},{name:'mark.svg',data:'b'}]),/Duplicate/);
});

test('delivery contains every approved static view and fixes micro geometry independently of selection',()=>{
  const entries=kit.catalog();assert.equal(entries.length,51);
  for(const order of [2,3])for(const variant of ['primary','monochrome','dark','light'])for(const view of Object.keys(brand.tokens.geometry.views))assert.ok(entries.some(e=>e.name===`svg/${order}x${order}/${variant}/${view}.svg`));
  const input={order:3,variant:'favicon',view:'bottom',size:256};
  const delivery=kit.assemble({selected:input,components});input.variant='dark';
  assert.deepEqual(delivery.manifest.selected,{order:2,variant:'favicon',view:'iso',size:256});
  assert.equal(delivery.files.find(f=>f.name==='selected/eva-selected.svg').data,assets.svg({variant:'favicon',size:256}));
  for(const entry of entries)assert.equal(delivery.files.find(f=>f.name===entry.name).data,assets.svg(entry.options));
  for(const selected of [{order:4},{view:'arbitrary'},{variant:'neon'},{size:17}])assert.throws(()=>kit.assemble({selected,components}),RangeError);
});

test('manifest describes every bundled byte; portable sources retain the canonical engine',()=>{
  const delivery=kit.assemble({components}),decoded=unzip(kit.zip(delivery.files));
  assert.equal(decoded.size,67);assert.equal(delivery.manifest.pngIncluded,false);
  for(const record of delivery.manifest.files){
    const content=decoded.get(record.path);assert.equal(content.length,record.bytes);
    assert.equal(kit.crc32(content).toString(16).padStart(8,'0'),record.crc32);
  }
  for(const name of kit.componentNames)assert.equal(decoded.get('components/'+name).toString('utf8'),components[name]);
  assert.deepEqual(JSON.parse(decoded.get('tokens.json')),brand.tokens);
  const html=decoded.get('preview.html').toString('utf8');assert.doesNotMatch(html,/https?:\/\/|\/static\/|\/api\//);
  for(const url of html.matchAll(/(?:src|href)="([^"]+)"/g)){
    if(url[1].startsWith('#'))assert.ok(html.includes(`id="${url[1].slice(1)}"`),url[1]);
    else assert.ok(decoded.has(url[1]),url[1]);
  }
  for(const code of html.matchAll(/<script>([\s\S]*?)<\/script>/g))assert.doesNotThrow(()=>new vm.Script(code[1]));
  const missing={...components};delete missing['cube-state.js'];assert.throws(()=>kit.assemble({components:missing}),/Missing component/);
});

for(const blocked of [false,true])test(`packaged theme runs before geometry, preserves legal state, and tolerates storage blocked=${blocked}`,()=>{
  const delivery=unzip(kit.zip(kit.assemble({components}).files));
  const html=delivery.get('preview.html').toString('utf8');
  assert.ok(html.indexOf('components/ui-theme.js')<html.indexOf('</head>'));
  assert.equal(delivery.get('components/ui-system.css').toString('utf8'),components['ui-system.css']);
  const properties={},attributes={},events=new Map(),mediaEvents=new Map();
  const element={lang:'zh-CN',style:{setProperty(k,v){properties[k]=v;}},setAttribute(k,v){attributes[k]=v;},getAttribute(k){return attributes[k]??null;}};
  const select={value:'',matches:()=>true,addEventListener(k,v){events.set('select:'+k,v);},removeEventListener(){}};
  const note={textContent:''};
  const media={matches:false,addEventListener(k,v){mediaEvents.set(k,v);},removeEventListener(){}};
  const context=vm.createContext({TextEncoder,Uint8Array,ArrayBuffer,Uint32Array,DataView,
    document:{documentElement:element,readyState:'complete',
      querySelectorAll(selector){return selector==='[data-theme-status]'?[note]:[select];},addEventListener(){},removeEventListener(){}},
    matchMedia:()=>media,localStorage:{getItem(){if(blocked)throw Error('denied');return 'dark';},setItem(){if(blocked)throw Error('denied');}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    addEventListener(k,v){events.set(k,v);},removeEventListener(){},dispatchEvent(){}});
  vm.runInContext(delivery.get('components/ui-theme.js').toString('utf8'),context);
  assert.equal(attributes['data-theme'],blocked?'light':'dark');
  for(const name of ['logo-tokens.js','cube-state.js'])vm.runInContext(delivery.get('components/'+name).toString('utf8'),context);
  vm.runInContext(`const cube=new CubeLogic.CubeState(3),journal=['R','U','F2'];for(const move of journal)cube.apply(move);const before=JSON.stringify(cube.snapshot());`,context);
  select.value='dark';events.get('select:change')();
  assert.equal(attributes['data-theme'],'dark');assert.equal(properties['--brand-cube-white'],'#ededeb');
  assert.equal(vm.runInContext('JSON.stringify(cube.snapshot())===before',context),true);
  assert.equal(vm.runInContext('journal.join(" ")',context),'R U F2');
  assert.equal(vm.runInContext('cube.isSolved()',context),false);
  if(blocked)assert.match(note.textContent,/无法保存/);
  select.value='system';events.get('select:change')();media.matches=false;mediaEvents.get('change')();
  assert.equal(attributes['data-theme'],'light');
  assert.equal(vm.runInContext('JSON.stringify(cube.snapshot())===before',context),true);
});

test('portable preview announces action boundaries rather than every move or continuous cycle',async()=>{
  const html=kit.assemble({components}).files.find(f=>f.name==='preview.html').data;
  assert.match(html,/<p id="status" aria-live="off">/);
  assert.match(html,/<p id="motionAnnouncement"[^>]+role="status"[^>]+aria-live="polite"/);
  const announcements=[],updates=[],elements=new Map();
  for(const id of ['rotor','order','status','motionAnnouncement','play','solve'])elements.set(id,{value:'',textContent:'',events:new Map(),replaceChildren(){},addEventListener(k,v){this.events.set(k,v);}});
  for(const [id,log] of [['status',updates],['motionAnnouncement',announcements]]){
    let value='';Object.defineProperty(elements.get(id),'textContent',{get:()=>value,set:next=>{value=next;log.push(next);}});
  }
  const instances=[],media={matches:false,events:new Map(),addEventListener(k,v){this.events.set(k,v);}};
  const context=vm.createContext({EvaBrand:brand,CubeLogic:require('../../ui/web/static/cube-state.js'),
    CubeRendering:{CubeAnimator:class{}},CubeLogoSystem:{CubeLogo:class{
      constructor(options){this.state=options.state;this.changed=options.onChange;instances.push(this);this.emit('idle',true);}
      emit(stage,solved=false,move=null){this.changed({stage,solved,move,index:1,total:14});}
      setReducedMotion(){return Promise.resolve();}pause(){this.emit('scrambling');}resume(){this.emit('scrambling');}
      play(){this.emit('intro');return Promise.resolve();}solve(){this.emit('idle',true);return Promise.resolve();}
    }},document:{hidden:false,getElementById:id=>elements.get(id),events:new Map(),addEventListener(k,v){this.events.set(k,v);}},matchMedia:()=>media});
  const code=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];vm.runInContext(code,context);
  await elements.get('play').events.get('click')();const started=announcements.length;
  for(let i=0;i<50;i++)instances[0].emit(i%2?'solving':'scrambling',false,'R');
  instances[0].emit('idle',true); // A solved cycle is still part of the continuous demonstration.
  assert.equal(announcements.length,started);assert.ok(updates.length>=52);
  await elements.get('solve').events.get('click')();assert.match(announcements.at(-1),/Returned to the standard state/);
  instances[0].emit('idle',true);const finished=announcements.length;instances[0].emit('idle',true);
  assert.equal(announcements.length,finished);
  instances[0].emit('error');assert.match(announcements.at(-1),/Move journal preserved/);
});

test('complete PNG delivery checks dimensions, chunk integrity and icon membership',()=>{
  const files=pngFiles(),selected={order:3,variant:'dark',view:'front',size:256};
  const delivery=kit.assemble({selected,components,pngFiles:files});
  assert.equal(delivery.files.length,71);assert.equal(delivery.manifest.pngIncluded,true);assert.deepEqual(delivery.manifest.selected,selected);
  assert.throws(()=>kit.assemble({selected,components,pngFiles:files.slice(1)}),/must include/);
  assert.throws(()=>kit.assemble({selected,components,pngFiles:pngFiles(512)}),/size does not match/);
  const corrupt=pngFiles();corrupt[1].data[36]^=1;assert.throws(()=>kit.assemble({selected,components,pngFiles:corrupt}),/checksum/);
  const truncated=pngFiles();truncated[1].data=truncated[1].data.subarray(0,33);assert.throws(()=>kit.assemble({selected,components,pngFiles:truncated}),/Incomplete/);
  const duplicate=pngFiles();duplicate[3]=duplicate[2];assert.throws(()=>kit.assemble({selected,components,pngFiles:duplicate}),/Duplicate/);
  const unknown=pngFiles();unknown[3].name='other/icon.png';assert.throws(()=>kit.assemble({selected,components,pngFiles:unknown}),/Unknown PNG/);
});
