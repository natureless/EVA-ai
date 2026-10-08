// Generated assets are derived from the same tokens and geometry as the live UI.
const fs = require('node:fs');
const path = require('node:path');
const assets = require('../ui/web/static/brand-assets.js');
const kit = require('../ui/web/static/brand-package.js');
const identity = require('../ui/web/static/brand-identity.js');
const target = path.resolve(__dirname, '../ui/web/static');
const check = process.argv.includes('--check');
let changed = false;
const generated = [
  ['eva-mark.svg', {order:2, variant:'primary', size:256}],
  ['eva-favicon.svg', {order:2, variant:'favicon', size:32}],
].map(([name,options])=>[name,assets.svg(options)+'\n']);
const components=Object.fromEntries(kit.componentNames.map(name=>[name,fs.readFileSync(path.join(target,name),'utf8')]));
const record={...kit.versions,brand:'EVA',provenance:kit.sourceProvenance(components),
  files:generated.map(([name,data])=>({path:name,bytes:Buffer.byteLength(data),sha256:identity.sha256(data)}))};
const recordPath=path.join(target,'brand-source.json');
if(!check&&fs.existsSync(recordPath)){
  const old=JSON.parse(fs.readFileSync(recordPath,'utf8'));
  identity.assertRuleRevision(old,record);
}
generated.push(['brand-source.json',JSON.stringify(record,null,2)+'\n']);
for (const [name, expected] of generated) {
  const file = path.join(target, name);
  if (check) {
    if (!fs.existsSync(file) || fs.readFileSync(file, 'utf8') !== expected) {
      console.error(`${name} is stale. Run node scripts/build_brand_assets.cjs`);
      changed = true;
    }
  } else {
    fs.writeFileSync(file, expected);
    console.log(`Generated ${name}`);
  }
}
if (changed) process.exitCode = 1;
