// Display preferences only. Imported files never own cube geometry, state or chat strategy.
(function(root,factory){
  const common=typeof module==='object'&&module.exports;
  const api=factory(common?require('./logo-tokens.js'):root.EvaBrand,common?require('./brand-identity.js'):root.EvaBrandIdentity);
  if(common)module.exports=api;else root.EvaBrandConfig=api;
})(globalThis,function(brand,identity){
  'use strict';
  const fields=Object.freeze(['variant','order','view','static','reduced','pngSize']);
  const defaults=Object.freeze({version:1,variant:'primary',order:2,view:'iso',static:false,reduced:false,pngSize:1024});
  const pngSizes=Object.freeze([256,512,1024,2048]),maxBytes=65536;
  class ConfigError extends Error{constructor(code){super(code);this.name='ConfigError';this.code=code;}}
  function shape(value,keys){
    if(!value||typeof value!=='object'||Array.isArray(value))throw new ConfigError('invalid-values');
    if(Object.keys(value).some(key=>!keys.includes(key)))throw new ConfigError('unexpected-fields');
    if(keys.some(key=>!Object.hasOwn(value,key)))throw new ConfigError('missing-fields');
  }
  function preferences(value){
    const input=value&&typeof value==='object'?value:{};
    return {...defaults,variant:brand.variants.includes(input.variant)?input.variant:defaults.variant,
      order:input.order===3?3:2,view:Object.hasOwn(brand.tokens.geometry.views,input.view)?input.view:defaults.view,
      static:input.static===true,reduced:input.reduced===true,pngSize:pngSizes.includes(input.pngSize)?input.pngSize:defaults.pngSize};
  }
  function validate(value){
    shape(value,['version',...fields]);
    if(value.version!==1)throw new ConfigError('unknown-version');
    if(!brand.variants.includes(value.variant)||![2,3].includes(value.order)||!Object.hasOwn(brand.tokens.geometry.views,value.view)||
      typeof value.static!=='boolean'||typeof value.reduced!=='boolean'||!pngSizes.includes(value.pngSize))throw new ConfigError('invalid-values');
    return Object.freeze({...value});
  }
  function serialize(value){
    const valid=validate(value);
    return JSON.stringify({format:'eva-brand-display',formatVersion:1,brandVersion:identity.versions.brandVersion,
      tokenSchemaVersion:identity.versions.tokenSchemaVersion,display:Object.fromEntries(fields.map(key=>[key,valid[key]]))},null,2)+'\n';
  }
  function parse(text){
    if(typeof text!=='string')throw new ConfigError('invalid-json');
    if(new TextEncoder().encode(text).length>maxBytes)throw new ConfigError('too-large');
    let input;try{input=JSON.parse(text);}catch(_){throw new ConfigError('invalid-json');}
    if(input&&Object.hasOwn(input,'version')){
      if(identity.versions.brandVersion.split('.')[0]!=='1')throw new ConfigError('unknown-version');
      return {value:validate(input),migrated:true};
    }
    shape(input,['format','formatVersion','brandVersion','tokenSchemaVersion','display']);
    if(input.format!=='eva-brand-display')throw new ConfigError('unknown-format');
    if(input.formatVersion!==1||input.tokenSchemaVersion!==identity.versions.tokenSchemaVersion||
      typeof input.brandVersion!=='string'||! /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(input.brandVersion)||
      input.brandVersion!==identity.versions.brandVersion)throw new ConfigError('unknown-version');
    shape(input.display,fields);
    return {value:validate({version:1,...input.display}),migrated:false};
  }
  const differences=(before,after)=>fields.filter(key=>before[key]!==after[key]).map(key=>({field:key,before:before[key],after:after[key]}));
  class Controller{
    constructor({initial=defaults,prepare=async()=>true,commit=()=>{},changed=()=>{}}={}){
      this.active=validate(initial);this.desired=this.active;this.previous=null;
      this.prepare=prepare;this.commit=commit;this.changed=changed;this.revision=0;this.pending=0;this.saved=true;this.presentationDirty=false;
    }
    notify(){this.changed({active:this.active,desired:this.desired,pending:this.pending,canUndo:!!this.previous});}
    persist(){this.presentationDirty=false;try{this.saved=this.commit(this.active)!==false;}catch(_){this.saved=false;}}
    async apply(value,{recordUndo=true,undo=false}={}){
      const desired=validate(value),revision=++this.revision;this.desired=desired;++this.pending;this.notify();
      try{
        const ready=await this.prepare(desired,()=>revision===this.revision);
        if(revision!==this.revision)return false;
        if(ready===false){this.desired=this.active;return false;}
        // Presentation choices may change during a legal restoration; the latest ones win.
        const next=validate({...desired,view:this.desired.view,pngSize:this.desired.pngSize});
        const before=this.active;
        if(undo)this.previous=null;else if(recordUndo&&differences(before,next).length)this.previous=before;
        this.active=next;this.desired=next;this.persist();return true;
      }catch(error){
        if(revision!==this.revision)return false;
        this.desired=this.active;throw error;
      }finally{--this.pending;if(!this.pending&&this.presentationDirty)this.persist();this.notify();}
    }
    present(patch){
      if(Object.keys(patch).some(key=>!['view','pngSize'].includes(key)))throw new ConfigError('unexpected-fields');
      const before=this.active,next=validate({...before,...patch});
      if(differences(before,next).length)this.previous=before;
      this.active=next;this.desired=validate({...this.desired,...patch});
      if(this.pending)this.presentationDirty=true;else this.persist();this.notify();
    }
    undo(){return this.previous?this.apply(this.previous,{recordUndo:false,undo:true}):Promise.resolve(false);}
  }
  async function preparePreview(preview,value,isCurrent=()=>true,systemReduced=false){
    const reduced=value.reduced||systemReduced;
    if(preview.reduced!==reduced)await preview.setReduced(reduced);
    if(!isCurrent())return false;
    return await preview.selectOrder(value.order)&&isCurrent();
  }
  return Object.freeze({fields,defaults,pngSizes,maxBytes,preferences,validate,serialize,parse,differences,Controller,preparePreview,ConfigError});
});
