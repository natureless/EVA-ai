"""Real graph/list/runtime reads in the established isolated MockLLM lifecycle."""
import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('isolator',ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py')
isolator=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolator)
isolator.OUT=OUT
sandbox,copied,blocked=isolator.prepare()
from app.main import app
from app.api_routes import routes_memory_graph
from fastapi.testclient import TestClient

checks=[];responses={};failure=None
started=datetime.now(timezone.utc).isoformat()
def check(name,condition,actual):
    checks.append({'name':name,'passed':bool(condition),'actual':actual})
    if not condition: raise AssertionError(name)
def read(client,name,url,params=None,method='GET',payload=None,expected=200):
    response=client.request(method,url,params=params,json=payload)
    data=response.json()
    responses[name]={'url':url,'params':params,'status':response.status_code,'body':data}
    check(name+' status',response.status_code==expected,response.status_code)
    return data
def seed(db):
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO events (id,type,source,timestamp,payload,status) VALUES ('g2-event','user_message','chat','2026-10-09',?, 'completed')",(json.dumps({'text':'G2隔离标记'}),))
        provenance=json.dumps({'schema_version':1,'epistemic_status':'user_statement','source':'chat','source_event_id':'g2-event'})
        connection.execute("INSERT INTO working_memory (id,content,summary,source,priority,tags_json,created_at,expires_at,provenance_json) VALUES ('g2-memory','G2隔离标记','fixture','chat',2,'[]','2026-10-09','2999-01-01',?)",(provenance,))
        for index in range(3):
            connection.execute("INSERT INTO world_entities (id,type,name,properties_json,updated_at) VALUES (?, 'task', ?, '{}', '2026-10-09')",(f'g2-world-{index}',f'G2隔离实体{index}'))
        for target,relation in [('g2-world-1','owns'),('g2-world-1','reviews'),('g2-world-2','uses'),('g2-missing','dangling')]:
            connection.execute("INSERT INTO world_edges (source,target,relation,weight,updated_at) VALUES ('g2-world-0',?,?,.9,'2026-10-09')",(target,relation))
def seeded_snapshot(db):
    with sqlite3.connect(db) as connection:
        return {table:connection.execute(f"SELECT * FROM {table} WHERE {column} LIKE 'g2-%' ORDER BY {column}").fetchall()
                for table,column in [('events','id'),('working_memory','id'),('world_entities','id'),('world_edges','source')]}

try:
    with TestClient(app) as client:
        client.headers['X-API-Token']=isolator.FIXTURE_TOKEN
        db=Path(app.state.container.store.db_path)
        check('database isolated',db.is_relative_to(sandbox),str(db))
        seed(db);before=seeded_snapshot(db)
        overview=read(client,'tiers','/api/memory/tiers',{'format':'explorer'})
        check('five real tier records',{x['name'] for x in overview['tiers']}=={'S1','S2','S3','S4','S5'},overview)
        entries=read(client,'entries','/api/memory/entries/S2',{'limit':50})
        check('seed visible in browse',any(x['id']=='g2-memory' for x in entries['entries']),entries)
        search=read(client,'search','/api/memory/search',method='POST',payload={'query':'G2隔离标记','tiers':['S2'],'limit':30})
        check('seed visible in read-only search',any(x['id']=='g2-memory' for x in search['results']['S2']),search)
        read(client,'empty-search','/api/memory/search',method='POST',payload={'query':'g2-no-match-unique','tiers':['S2'],'limit':30})
        graph=read(client,'graph','/api/memory/graph',{'limit':50})
        check('graph seeded records',{'S2:g2-memory','S4:g2-world-0','S5:g2-event'} <= {n['id'] for n in graph['nodes']},graph['counts'])
        check('explicit provenance relation',any(e['source']=='S2:g2-memory' and e['target']=='S5:g2-event' and e['kind']=='provenance' for e in graph['edges']),graph['edges'])
        detail=read(client,'detail','/api/memory/graph/node',{'tier':'S4','record_id':'g2-world-0'})
        check('detail id','S4:g2-world-0'==detail['id'],detail['id'])
        pages=[];offset=0
        while True:
            page=read(client,'neighbors-'+str(offset),'/api/memory/graph/neighbors',{'tier':'S4','record_id':'g2-world-0','limit':1,'offset':offset})
            pages.append(page)
            if page['next_offset'] is None:break
            offset=page['next_offset']
            assert offset<=3
        responses['neighbor-pages']=pages
        check('pages preserve distinct relations',{e['relation'] for p in pages for e in p['edges']}=={'owns','reviews','uses'},pages)
        read(client,'neighbors-full','/api/memory/graph/neighbors',{'tier':'S4','record_id':'g2-world-0','limit':50,'offset':0})
        empty=read(client,'empty-graph','/api/memory/graph',{'q':'g2-no-match-unique'})
        check('valid empty projection',empty['nodes']==[] and empty['edges']==[],empty['scope'])
        read(client,'missing-detail','/api/memory/graph/node',{'tier':'S4','record_id':'g2-no-match-unique'},expected=404)
        read(client,'invalid-tier','/api/memory/graph',{'tiers':'invalid'},expected=422)
        original=routes_memory_graph._sources
        def missing_source(request):
            _,session,vault=original(request)
            return sandbox/'data/g2-unavailable.db',session,vault
        with patch.object(routes_memory_graph,'_sources',missing_source):
            read(client,'unavailable-graph','/api/memory/graph',expected=503)
        recovered=read(client,'recovered-graph','/api/memory/graph',{'limit':50})
        check('recovery preserved projection',recovered['nodes']==graph['nodes'] and recovered['edges']==graph['edges'],{'nodes':len(recovered['nodes'])})
        runtime=read(client,'runtime','/api/runtime')
        check('observed runtime consumer',runtime['mode']=='legacy' and runtime['consumer_running'] is True and runtime['accepting_events'] is True,runtime)
        diagnostic=read(client,'diagnostic','/health/diagnostic')
        check('mock model health not asserted',diagnostic['overall']!='healthy',diagnostic)
        check('seeded persistent rows unchanged by reads',before==seeded_snapshot(db),{'tables':list(before),'scope':'Only seeded rows; not a blanket assertion about all lifecycle persistence'})
    check('no guarded external access',not blocked,blocked)
except BaseException as exc:
    failure=repr(exc)
finally:
    result={'status':'failed' if failure else 'passed','startedAt':started,'finishedAt':datetime.now(timezone.utc).isoformat(),
        'sandbox':str(sandbox),'copiedUiHashes':copied,'checks':checks,'responses':responses,'error':failure,'blockedAuditEvents':blocked,
        'scope':'Actual ASGI TestClient and full isolated app/bootstrap/SQLite, MockLLM, no browser or native network claim',
        'controlHooks':['Known fixture rows seeded into isolated database','One graph read source temporarily replaced with a nonexistent path inside sandbox, then restored']}
    path=sandbox/'memory-api.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':len(checks),'path':str(path),'error':failure},ensure_ascii=False))
if failure:sys.exit(1)
