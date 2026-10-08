"""Real application WS/SSE contracts; gated MockLLM and fresh isolated SQLite."""
import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('isolator', ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py')
isolator=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolator)
isolator.OUT=OUT
sandbox,copied,blocked=isolator.prepare()
from app.main import app
from core.llm_adapter import MockLLM
from fastapi.testclient import TestClient
import agents.chat_agent as chat_agent

original=chat_agent.get_llm
checks=[];cases=[];failure=None
started=datetime.now(timezone.utc).isoformat()

def check(name,passed,actual):
    checks.append(dict(name=name,passed=bool(passed),actual=actual))
    if not passed:raise AssertionError(name)

class Gate(MockLLM):
    def __init__(self):
        self.entered=threading.Event();self.release=threading.Event()
    def chat(self,messages):
        self.entered.set()
        if not self.release.wait(8):raise TimeoutError('Mock response gate was not released')
        return super().chat(messages)

def terminal(client,task):
    until=time.monotonic()+5
    while time.monotonic()<until:
        response=client.get('/api/chat/result/'+task)
        if response.status_code!=202:return response
        time.sleep(.02)
    raise TimeoutError('No terminal receipt')

try:
    with TestClient(app) as client:
        client.headers['X-API-Token']=isolator.FIXTURE_TOKEN
        registry=app.state.container.result_registry
        for mode in ('normal','deep'):
            for transport in ('WS','SSE'):
                gate=Gate();chat_agent.get_llm=lambda:gate
                request=dict(text=f'隔离{mode}{transport}请求',mode=mode,remember=False)
                case=dict(mode=mode,transport=transport,request=request,selectedNextMode='deep' if mode=='normal' else 'normal')
                pool=ThreadPoolExecutor(max_workers=2)
                try:
                    if transport=='WS':
                        with client.websocket_connect('/ws?channel=chat_reply',headers={'X-API-Token':isolator.FIXTURE_TOKEN}) as ws:
                            received=pool.submit(ws.receive_json)
                            ack=client.post('/api/chat',json=request)
                            case['ack']=ack.json();task=case['ack']['task_id']
                            check(mode+' WS admitted',ack.status_code==200 and case['ack']['accepted'] and case['ack']['mode']==mode,case['ack'])
                            check(mode+' WS gated',gate.entered.wait(5),{'modelGate':'entered'})
                            pending=client.get('/api/chat/result/'+task)
                            case['pending']=pending.json()
                            check(mode+' WS pending',pending.status_code==202 and not case['pending']['completed'],case['pending'])
                            gate.release.set()
                            delivered=received.result(timeout=5)
                            case['wsMessage']=delivered
                            receipt=delivered['payload']
                            check(mode+' WS identity',delivered['channel']=='chat_reply' and receipt['task_id']==task,{'task_id':receipt.get('task_id')})
                    else:
                        before=set(registry._pending)
                        posted=pool.submit(client.post,'/api/chat/stream',json=request)
                        check(mode+' SSE gated',gate.entered.wait(5),{'modelGate':'entered'})
                        with registry._lock:new=set(registry._pending)-before
                        check(mode+' SSE one admission',len(new)==1,{'newTasks':list(new)})
                        task=new.pop()
                        pending=client.get('/api/chat/result/'+task);case['pending']=pending.json()
                        check(mode+' SSE pending',pending.status_code==202 and not case['pending']['completed'],case['pending'])
                        gate.release.set()
                        response=posted.result(timeout=5)
                        case['wire']=response.text;case['headerTaskId']=response.headers.get('X-EVA-Task-ID')
                        receipt=json.loads(response.text.split('event: result\ndata: ',1)[1].split('\n\n',1)[0])
                        check(mode+' SSE identity',response.status_code==200 and case['headerTaskId']==task and receipt['task_id']==task,{'task_id':task,'header':case['headerTaskId']})
                    case['receipt']=receipt;case['taskId']=task
                    check(mode+' '+transport+' fixed mode',receipt['mode']==mode and receipt['mode_info']['mode']==mode and receipt['ok'] is True,receipt)
                    polled=terminal(client,task);case['polled']=polled.json()
                    check(mode+' '+transport+' retained receipt',polled.status_code==200 and case['polled']['reply']==receipt['reply'] and case['polled']['mode']==mode,case['polled'])
                    cases.append(case)
                finally:
                    gate.release.set();pool.shutdown(wait=False,cancel_futures=True)
        chat_agent.get_llm=original
        for kind,text,status in [('Chinese boundary','界'*4000,200),('emoji boundary','🙂'*4000,200),('over boundary','界'*4001,422)]:
            response=client.post('/api/chat',json={'text':text,'mode':'normal','remember':False})
            check(kind,response.status_code==status,{'status':response.status_code,'codePoints':len(text),'utf16Units':len(text.encode('utf-16-le'))//2})
            if status==200:terminal(client,response.json()['task_id'])
        missing=client.get('/api/chat/result/no-such-test-request')
        check('missing receipt',missing.status_code==404 and missing.json()['completed'] is False,missing.json())
        maximum=registry.max_entries
        try:
            registry.max_entries=len(registry._pending)
            depth=app.state.container.event_bus.size()
            rejected=client.post('/api/chat',json={'text':'隔离容量拒绝','mode':'deep'})
            check('known admission rejection',rejected.status_code==503 and rejected.json()['accepted'] is False,{'status':rejected.status_code,'receipt':rejected.json()})
            check('rejection not queued',app.state.container.event_bus.size()==depth,{'before':depth,'after':app.state.container.event_bus.size()})
        finally:registry.max_entries=maximum
        gate=Gate();chat_agent.get_llm=lambda:gate
        previous_timeout=registry.pending_timeout_sec
        try:
            registry.pending_timeout_sec=.15
            ack=client.post('/api/chat',json={'text':'隔离等待超时','mode':'deep'})
            task=ack.json()['task_id']
            check('timeout worker running',gate.entered.wait(5),{'task_id':task})
            time.sleep(.18)
            unknown=client.get('/api/chat/result/'+task).json()
            check('started deadline remains uncertain',unknown['terminal_state']=='outcome_unknown' and unknown['execution_state']=='may_still_be_running',unknown)
            gate.release.set()
            late=registry.fulfill(task,{'ok':True,'reply':'controlled late completion'})
            again=client.get('/api/chat/result/'+task).json()
            check('late result cannot replace terminal',late is False and again['terminal_state']=='outcome_unknown',{'lateAccepted':late,'receipt':again})
        finally:
            gate.release.set();registry.pending_timeout_sec=previous_timeout;chat_agent.get_llm=original
except Exception as exc:
    failure={'type':type(exc).__name__,'message':str(exc)}
finally:
    chat_agent.get_llm=original
report=dict(candidateId=OUT.name,startedAt=started,finishedAt=datetime.now(timezone.utc).isoformat(),
    status='failed' if failure else 'passed',checks=checks,cases=cases,failure=failure,
    sandbox=str(sandbox),templateHashes=copied,blockedAuditEvents=blocked,
    scope='actual API/application lifecycle with gated MockLLM; TestClient WS/SSE, not browser rendering',
    injections=['ChatAgent MockLLM response gate','temporary receipt capacity','temporary pending deadline','controlled late registry fulfillment'],
    restored='ChatAgent factory and registry settings restored; TestClient lifespan shut down',
    limits=['real remote model','browser input/paint/focus','literal TCP stream disconnect','enabled goal/ACT profiles'])
isolator.write(sandbox/'api-contracts.json',report)
print(json.dumps(dict(status=report['status'],checks=len(checks),cases=len(cases),report=str(sandbox/'api-contracts.json'),failure=failure),ensure_ascii=False))
if failure:raise SystemExit(1)
