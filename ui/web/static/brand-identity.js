// Shared delivery versions and deterministic provenance; no network or crypto provider required.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;else root.EvaBrandIdentity=api;
})(globalThis,function(){
  'use strict';
  const versions=Object.freeze({formatVersion:2,brandVersion:'1.0.0',tokenSchemaVersion:1,componentVersion:'1.1.3'});
  const encoder=new TextEncoder();
  function canonical(value,seen=new Set()){
    if(value===null||typeof value==='string'||typeof value==='boolean')return JSON.stringify(value);
    if(typeof value==='number'&&Number.isFinite(value))return JSON.stringify(value);
    if(typeof value!=='object'||seen.has(value))throw new TypeError('Provenance requires finite, acyclic JSON');
    if(!Array.isArray(value)&&Object.prototype.toString.call(value)!=='[object Object]')throw new TypeError('Provenance requires JSON objects');
    seen.add(value);
    const text=Array.isArray(value)?'['+Array.from(value,item=>canonical(item,seen)).join(',')+']':
      '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key],seen)).join(',')+'}';
    seen.delete(value);return text;
  }
  const constants=new Uint32Array([
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
  const rotate=(x,n)=>(x>>>n)|(x<<(32-n));
  function sha256(input){
    const data=typeof input==='string'?encoder.encode(input):input;
    if(!(data instanceof Uint8Array))throw new TypeError('SHA-256 input must be text or bytes');
    const padded=new Uint8Array(Math.ceil((data.length+9)/64)*64);padded.set(data);padded[data.length]=128;
    const view=new DataView(padded.buffer),bits=data.length*8;
    view.setUint32(padded.length-8,Math.floor(bits/4294967296));view.setUint32(padded.length-4,bits>>>0);
    const h=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]),w=new Uint32Array(64);
    for(let offset=0;offset<padded.length;offset+=64){
      for(let i=0;i<16;i++)w[i]=view.getUint32(offset+i*4);
      for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(rotate(a,7)^rotate(a,18)^(a>>>3))+w[i-7]+(rotate(b,17)^rotate(b,19)^(b>>>10)))>>>0;}
      let [a,b,c,d,e,f,g,j]=h;
      for(let i=0;i<64;i++){
        const t1=(j+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[i]+w[i])>>>0;
        const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
        j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
      }
      [a,b,c,d,e,f,g,j].forEach((v,i)=>{h[i]=(h[i]+v)>>>0;});
    }
    return Array.from(h,v=>v.toString(16).padStart(8,'0')).join('');
  }
  const fingerprint=value=>sha256(canonical(value));
  function provenance(brand,components){
    const palettes=Object.fromEntries(brand.variants.map(name=>[name,brand.palette(name)]));
    const sources=Object.fromEntries(Object.keys(components).sort().map(name=>[name,sha256(components[name])]));
    return {algorithm:'SHA-256',canonicalization:'eva-sorted-json-v1',
      geometrySha256:fingerprint(brand.tokens.geometry),tokensSha256:fingerprint(brand.tokens),
      rulesSha256:fingerprint({tokens:brand.tokens,palettes}),componentSetSha256:fingerprint(sources),sources};
  }
  function inspectManifest(manifest,{historical=false}={}){
    if(!manifest||manifest.brand!=='EVA')throw new TypeError('Not an EVA brand manifest');
    if(manifest.formatVersion===1)return {legacy:true,provenanceAvailable:false,formatVersion:1};
    if(manifest.formatVersion!==versions.formatVersion)throw new RangeError('Unsupported brand package format version');
    for(const key of ['brandVersion','componentVersion']){
      if(typeof manifest[key]!=='string'||! /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(manifest[key])||
        manifest[key].split('.').some(v=>!Number.isSafeInteger(Number(v)))||
        (historical?Number(manifest[key].split('.')[0])>Number(versions[key].split('.')[0]):
          manifest[key].split('.')[0]!==versions[key].split('.')[0]))throw new RangeError('Unsupported '+key);
    }
    if(manifest.tokenSchemaVersion!==versions.tokenSchemaVersion)throw new RangeError('Unsupported token schema version');
    const p=manifest.provenance;
    if(!p||p.algorithm!=='SHA-256'||p.canonicalization!=='eva-sorted-json-v1')throw new TypeError('Missing or unsupported provenance');
    for(const key of ['geometrySha256','tokensSha256','rulesSha256','componentSetSha256']){
      if(typeof p[key]!=='string'||! /^[a-f0-9]{64}$/.test(p[key]))throw new TypeError('Invalid provenance fingerprint');
    }
    if(!p.sources||Array.isArray(p.sources)||typeof p.sources!=='object'||!Object.keys(p.sources).length||
      Object.values(p.sources).some(v=>typeof v!=='string'||! /^[a-f0-9]{64}$/.test(v))||
      fingerprint(p.sources)!==p.componentSetSha256)throw new TypeError('Invalid component provenance');
    return {legacy:false,provenanceAvailable:true,...versions,brandVersion:manifest.brandVersion,componentVersion:manifest.componentVersion};
  }
  function assertRuleRevision(previous,next){
    const old=inspectManifest(previous,{historical:true});inspectManifest(next);
    if(old.legacy||previous.provenance.rulesSha256===next.provenance.rulesSha256)return;
    const a=previous.brandVersion.split('.').map(Number),b=next.brandVersion.split('.').map(Number);
    const first=a.findIndex((value,i)=>value!==b[i]);
    if(first<0||b[first]<=a[first])throw new Error('Brand rules changed. Review the change and increment brandVersion before generating.');
  }
  function changelog(){return '# EVA Brand Changelog\n\n## Brand 1.0.0 / Components 1.1.3 - 2026-10-08\n\nShared presentation adopts platform system fonts, neutral surfaces, readable auxiliary labels and forced-colors focus and selection. Product chat discloses demo controls on demand; brand settings discloses geometry separately. Master geometry and legal motion remain unchanged.\n\n## Brand 1.0.0 / Components 1.1.2 - 2026-10-08\n\nShared presentation improves typography and interactive surfaces. Product chat adds explicit reading navigation; settings adds section navigation; memory overlays preserve selection on Escape. Cube geometry and legal motion contracts remain unchanged.\n\n## Brand 1.0.0 / Components 1.1.1 — 2026-10-01\n\nPortable preview separates visual move status from polite action announcements. Continuous turns do not repeat live-region updates; start, restoration, model changes, reduced motion and errors remain announced. Geometry and legal motion contracts are unchanged.\n\n## Brand 1.0.0 / Components 1.1.0 — 2026-10-01\n\nPortable preview now includes the shared product presentation tokens and light/dark/system theme controller. Theme changes preserve the cube state and journal. Two additional local presentation files are tracked by source and file fingerprints. Geometry, motion API, token schema and package format remain compatible.\n\n## Brand 1.0.0 / Components 1.0.0 — 2026-09-30\n\nFirst explicit version baseline for the existing 2×2 / 3×3 geometry, monochrome variants, legal move journal and inverse restoration. Geometry and motion contracts are unchanged.\n\nPackage format 2 adds separate brand, token schema and component versions; normalized rule fingerprints and source/file SHA-256 fingerprints. Format 1 remains a legacy package without provenance.\n';}
  return Object.freeze({versions,canonical,sha256,fingerprint,provenance,inspectManifest,assertRuleRevision,changelog});
});
