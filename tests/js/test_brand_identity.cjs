const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const identity=require('../../ui/web/static/brand-identity.js');
const brand=require('../../ui/web/static/logo-tokens.js');
const kit=require('../../ui/web/static/brand-package.js');
const root=path.resolve(__dirname,'../../ui/web/static');
const components=Object.fromEntries(kit.componentNames.map(name=>[name,fs.readFileSync(path.join(root,name),'utf8')]));
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');

test('portable SHA-256 matches independent Node crypto across UTF-8, binary and block boundaries',()=>{
  for(const value of ['', 'abc','EVA · 魔方\n','a'.repeat(55),'a'.repeat(56),'a'.repeat(64),'a'.repeat(1000000),new Uint8Array([0,255,128,13,10])])assert.equal(identity.sha256(value),hash(value));
});
test('normalized JSON ignores object insertion order, preserves arrays and rejects ambiguous inputs',()=>{
  assert.equal(identity.canonical({z:[{b:2,a:1},null],a:-0}),'{'+'"a":0,"z":[{"a":1,"b":2},null]}');
  assert.equal(identity.fingerprint({b:2,a:1}),identity.fingerprint({a:1,b:2}));
  assert.notEqual(identity.fingerprint([1,2]),identity.fingerprint([2,1]));
  const cycle={};cycle.self=cycle;
  for(const value of [cycle,{x:undefined},Infinity,NaN,new Date(),[,1]])assert.throws(()=>identity.fingerprint(value),TypeError);
});
test('geometry, token, palette and source changes have distinct traceable fingerprints',()=>{
  const original=identity.provenance(brand,components),tokens=JSON.parse(JSON.stringify(brand.tokens));
  tokens.geometry.orders[2].gap+=1;
  const geometry=identity.provenance({...brand,tokens},components);
  assert.notEqual(original.geometrySha256,geometry.geometrySha256);assert.notEqual(original.tokensSha256,geometry.tokensSha256);
  assert.equal(original.componentSetSha256,geometry.componentSetSha256);
  const palette=identity.provenance({...brand,palette:n=>({...brand.palette(n),front:'#123456'})},components);
  assert.equal(original.tokensSha256,palette.tokensSha256);assert.notEqual(original.rulesSha256,palette.rulesSha256);
  const source=identity.provenance(brand,{...components,'cube-state.js':components['cube-state.js']+'\n// revision'});
  assert.equal(original.rulesSha256,source.rulesSha256);assert.notEqual(original.componentSetSha256,source.componentSetSha256);
});
test('browser UMD and CLI create identical versions, normalized provenance and full package bytes',()=>{
  const context=vm.createContext({TextEncoder,Uint8Array,ArrayBuffer,Uint32Array,DataView});
  for(const name of ['logo-tokens.js','cube-state.js','brand-assets.js','brand-identity.js','brand-package.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context);
  context.sources=components;
  const browser=vm.runInContext('EvaBrandPackage.assemble({components:sources,selected:{order:3,variant:"dark",view:"bottom",size:256}})',context);
  const cli=kit.assemble({components,selected:{order:3,variant:'dark',view:'bottom',size:256}});
  assert.equal(JSON.stringify(browser.manifest),JSON.stringify(cli.manifest));
  assert.deepEqual(Buffer.from(vm.runInContext('EvaBrandPackage.zip(EvaBrandPackage.assemble({components:sources,selected:{order:3,variant:"dark",view:"bottom",size:256}}).files)',context)),Buffer.from(kit.zip(cli.files)));
});
test('manifest hashes describe actual source and output bytes, including generated styles and selected assets',()=>{
  const delivery=kit.assemble({components});
  assert.deepEqual(kit.inspectManifest(delivery.manifest),{legacy:false,provenanceAvailable:true,...identity.versions});
  for(const record of delivery.manifest.files)assert.equal(record.sha256,hash(delivery.files.find(f=>f.name===record.path).data));
  for(const [name,digest] of Object.entries(delivery.manifest.provenance.sources))assert.equal(digest,hash(delivery.files.find(f=>f.name==='components/'+name).data));
  const html=delivery.files.find(f=>f.name==='preview.html').data;
  assert.ok(html.includes(`Brand ${identity.versions.brandVersion} · Components ${identity.versions.componentVersion}`));assert.match(html,/Package format 2/);
  assert.equal(delivery.files.find(f=>f.name==='CHANGELOG.md').data,identity.changelog());
});
test('manifest inspection distinguishes legacy provenance, rejects unknown contracts and malformed fingerprints',()=>{
  assert.deepEqual(kit.inspectManifest({brand:'EVA',formatVersion:1}),{legacy:true,provenanceAvailable:false,formatVersion:1});
  const valid=kit.assemble({components}).manifest;
  for(const patch of [{formatVersion:3},{tokenSchemaVersion:2},{brandVersion:'2.0.0'},{componentVersion:'2.0.0'},{brandVersion:'1.0.0<script>'},{provenance:null}])assert.throws(()=>kit.inspectManifest({...valid,...patch}));
  assert.throws(()=>kit.inspectManifest({...valid,provenance:{...valid.provenance,tokensSha256:'crc32'}}),/fingerprint/);
  assert.throws(()=>kit.inspectManifest({...valid,provenance:{...valid.provenance,sources:{'cube-state.js':'0'.repeat(64)}}}),/component provenance/);
});
test('source record detects mixed loaded rules, stale versions and source revisions before delivery',()=>{
  const record={brand:'EVA',...kit.versions,provenance:kit.sourceProvenance(components)};
  assert.doesNotThrow(()=>kit.assertSource(record,components));
  assert.throws(()=>kit.assertSource({...record,brandVersion:'1.0.1'},components),/sources changed/);
  assert.throws(()=>kit.assertSource(record,{...components,'cube-logo.js':components['cube-logo.js']+'\n'}),/sources changed/);
  const provenance={...record.provenance,rulesSha256:'0'.repeat(64)};
  assert.throws(()=>kit.assertSource({...record,provenance},components),/sources changed/);
});
test('checked-in source record and generated navigation assets match the current rule and source baseline',()=>{
  const record=JSON.parse(fs.readFileSync(path.join(root,'brand-source.json'),'utf8'));
  kit.assertSource(record,components);
  for(const file of record.files){const data=fs.readFileSync(path.join(root,file.path));assert.equal(data.length,file.bytes);assert.equal(hash(data),file.sha256);}
});
test('changed brand rules cannot be regenerated under the same or an older brand version',()=>{
  const previous=kit.assemble({components}).manifest;
  const next={...previous,provenance:{...previous.provenance,rulesSha256:'0'.repeat(64)}};
  assert.throws(()=>identity.assertRuleRevision(previous,next),/increment brandVersion/);
  assert.throws(()=>identity.assertRuleRevision(previous,{...next,brandVersion:'0.9.0'}),/Unsupported brandVersion/);
  assert.doesNotThrow(()=>identity.assertRuleRevision(previous,{...next,brandVersion:'1.0.1'}));
  assert.throws(()=>identity.assertRuleRevision({...previous,brandVersion:'1.1.0'},{...next,brandVersion:'1.0.1'}),/increment brandVersion/);
  assert.doesNotThrow(()=>identity.assertRuleRevision(previous,{...previous,provenance:kit.sourceProvenance({...components,'cube-logo.js':components['cube-logo.js']+'\n// compatible source revision'})}));
});
test('release generation compares a known historical major without treating it as runtime-compatible',()=>{
  const context=vm.createContext({TextEncoder,Uint8Array,Uint32Array,DataView});
  vm.runInContext(fs.readFileSync(path.join(root,'brand-identity.js'),'utf8').replace("brandVersion:'1.0.0'","brandVersion:'2.0.0'"),context);
  const historical=kit.assemble({components}).manifest;
  const next={...historical,brandVersion:'2.0.0',provenance:{...historical.provenance,rulesSha256:'0'.repeat(64)}};
  assert.throws(()=>context.EvaBrandIdentity.inspectManifest(historical),/Unsupported brandVersion/);
  assert.doesNotThrow(()=>context.EvaBrandIdentity.assertRuleRevision(historical,next));
  assert.throws(()=>context.EvaBrandIdentity.assertRuleRevision({...historical,brandVersion:'3.0.0'},next),/Unsupported brandVersion/);
});
test('real generation command fails stale sources and refuses rule changes without a version increment',()=>{
  const base=path.resolve(__dirname,'../../artifacts/brand-studio/brand01');fs.mkdirSync(base,{recursive:true});
  const fixture=fs.mkdtempSync(path.join(base,'generation-check-'));
  try{
    const staticRoot=path.join(fixture,'ui/web/static');fs.mkdirSync(staticRoot,{recursive:true});
    fs.mkdirSync(path.join(fixture,'scripts'));
    for(const name of [...kit.componentNames,'brand-package.js'])fs.copyFileSync(path.join(root,name),path.join(staticRoot,name));
    fs.copyFileSync(path.resolve(__dirname,'../../scripts/build_brand_assets.cjs'),path.join(fixture,'scripts/build_brand_assets.cjs'));
    const run=(...args)=>spawnSync(process.execPath,['scripts/build_brand_assets.cjs',...args],{cwd:fixture,encoding:'utf8'});
    assert.equal(run().status,0);assert.equal(run('--check').status,0);
    fs.appendFileSync(path.join(staticRoot,'cube-logo.js'),'\n// source-only revision\n');
    assert.equal(run('--check').status,1);assert.equal(run().status,0);
    const tokens=path.join(staticRoot,'logo-tokens.js');fs.writeFileSync(tokens,fs.readFileSync(tokens,'utf8').replace('hold:700','hold:701'));
    assert.equal(run('--check').status,1);
    const refusal=run();assert.equal(refusal.status,1);assert.match(refusal.stderr,/increment brandVersion/);
    const identityFile=path.join(staticRoot,'brand-identity.js');
    fs.writeFileSync(identityFile,fs.readFileSync(identityFile,'utf8').replace("brandVersion:'1.0.0'","brandVersion:'1.0.1'"));
    assert.equal(run().status,0);assert.equal(run('--check').status,0);
  }finally{
    // Only remove this uniquely created fixture directly within the intended artifact directory.
    assert.equal(path.dirname(fs.realpathSync(fixture)).toLowerCase(),fs.realpathSync(base).toLowerCase());
    assert.ok(path.basename(fixture).startsWith('generation-check-'));
    fs.rmSync(fixture,{recursive:true,force:true});
  }
});
