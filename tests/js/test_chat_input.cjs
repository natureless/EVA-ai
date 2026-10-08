const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function harness(stream = true) {
  const node = () => ({value:'',style:{},attrs:{},events:{},hidden:true,textContent:'',
    setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
    addEventListener(k,fn){this.events[k]=fn;},focus(){this.focused=true;}});
  const ids = Object.fromEntries(['chatForm','chatInput','sendBtn','rememberToggle',
    'streamToggle','chatInputFeedback','chatActionFeedback'].map(id=>[id,node()]));
  ids.streamToggle.checked=stream;ids.rememberToggle.checked=true;
  let lang='zh';const calls=[];
  const context={document:{getElementById:id=>ids[id]||null},Event,
    I18N:{lang:()=>lang},ParticleController:{getConversationMode:()=> 'deep',setResponding(mode){calls.push({kind:'motion',mode});}}};
  const source=fs.readFileSync(path.join(__dirname,'../../ui/web/static/chat.js'),'utf8');
  vm.runInNewContext(source.slice(0,source.indexOf('// ── Init'))+'\nthis.chat=Chat;',context);
  const chat=context.chat;chat._renderMessages=()=>{};
  chat._addMessage=(role,text,mode)=>calls.push({kind:'message',role,text,mode});
  chat._sendStream=chat._sendViaWS=async(text,remember,generation,mode)=>calls.push({kind:'request',text,remember,mode});
  chat._bindForm();
  return {chat,ids,calls,submit:()=>ids.chatForm.events.submit({preventDefault(){}}),
    english(){lang='en';chat._renderInputError();}};
}

for(const stream of [false,true]) test(`${stream?'SSE':'WS'} oversize submission preserves draft and consent without starting response`,async()=>{
  const h=harness(stream);const draft='界'.repeat(4001);h.ids.chatInput.value=draft;
  await h.submit();
  assert.equal(h.ids.chatInput.value,draft);
  assert.equal(h.ids.rememberToggle.checked,true);
  assert.equal(h.calls.length,0);
  assert.equal(h.ids.chatInput.attrs['aria-invalid'],'true');
  assert.equal(h.ids.chatInputFeedback.hidden,false);
  assert.match(h.ids.chatInputFeedback.textContent,/4,000/);
  assert.match(h.ids.chatInputFeedback.textContent,/4,001/);
  assert.equal(h.ids.chatInput.focused,true);
  h.english();assert.match(h.ids.chatInputFeedback.textContent,/draft.*kept/i);
});

test('4000 Unicode code points can be sent despite using 8000 UTF-16 units',async()=>{
  const h=harness();const text='🙂'.repeat(4000);h.ids.chatInput.value=text;
  await h.submit();
  const request=h.calls.find(c=>c.kind==='request');
  assert.equal(request.text,text);assert.equal(request.remember,true);assert.equal(request.mode,'deep');
  assert.equal(h.ids.chatInput.value,'');assert.equal(h.ids.rememberToggle.checked,false);
});

test('editing an oversized draft clears its error once the sent text is within the limit',async()=>{
  const h=harness();h.ids.chatInput.value='界'.repeat(4001);await h.submit();
  h.ids.chatInput.value='界'.repeat(4000);h.ids.chatInput.events.input();
  assert.equal(h.ids.chatInput.attrs['aria-invalid'],undefined);
  assert.equal(h.ids.chatInputFeedback.hidden,true);
  assert.equal(h.calls.length,0);assert.equal(h.ids.rememberToggle.checked,true);
});

test('direct submission and explicit retry cannot bypass the server length contract',async()=>{
  const h=harness();h.ids.chatInput.value='保留正在编辑的草稿';
  const text='界'.repeat(4001);
  assert.equal(await h.chat._submit(text,'deep',true),false);
  const old={status:'rejected',request:{text,mode:'deep',stream:false}};
  assert.equal(await h.chat._retryMessage(old),false);
  assert.notEqual(old.retried,true);assert.equal(h.calls.length,0);
  assert.equal(h.ids.chatInput.value,'保留正在编辑的草稿');
});
