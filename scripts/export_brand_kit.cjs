// Portable source + SVG delivery; browser workspace additionally supplies canvas PNGs.
const fs=require('node:fs');
const path=require('node:path');
const kit=require('../ui/web/static/brand-package.js');
const source=path.resolve(__dirname,'../ui/web/static');
const options={order:2,variant:'primary',view:'iso',size:1024};
let output=path.resolve(__dirname,`../artifacts/brand-studio/eva-brand-kit-${kit.versions.brandVersion}-source.zip`);
try{
  const args=process.argv.slice(2),allowed=new Set(['--out','--order','--variant','--view','--size']);
  for(let i=0;i<args.length;i+=2){
    const flag=args[i],value=args[i+1];
    if(!allowed.has(flag)||!value)throw new Error('Usage: node scripts/export_brand_kit.cjs [--out file.zip] [--order 2|3] [--variant primary|monochrome|dark|light|favicon] [--view front|side|top|iso|oblique|bottom] [--size 256|512|1024|2048]');
    if(flag==='--out')output=path.resolve(value);
    else options[flag.slice(2)]=['--order','--size'].includes(flag)?Number(value):value;
  }
  const components=Object.fromEntries(kit.componentNames.map(name=>[name,fs.readFileSync(path.join(source,name),'utf8')]));
  kit.assertSource(JSON.parse(fs.readFileSync(path.join(source,'brand-source.json'),'utf8')),components);
  const delivery=kit.assemble({selected:options,components});
  fs.mkdirSync(path.dirname(output),{recursive:true});
  fs.writeFileSync(output,kit.zip(delivery.files));
  console.log(`Exported ${delivery.files.length} files: ${output}`);
  console.log(`Brand ${delivery.manifest.brandVersion} · Components ${delivery.manifest.componentVersion} · Format ${delivery.manifest.formatVersion}`);
  console.log(`Rules SHA-256: ${delivery.manifest.provenance.rulesSha256}`);
  console.log('For the complete PNG delivery, use the brand workspace ZIP download.');
}catch(error){console.error(error.message);process.exitCode=1;}
