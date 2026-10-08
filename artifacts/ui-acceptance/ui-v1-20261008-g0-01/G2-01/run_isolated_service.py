"""Exercise real EVA routes/bootstrap with a fresh isolated store and MockLLM.

python artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py
Add --serve --port 8784 for a loopback service; no real credentials or .env loaded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import url2pathname

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
FIXTURE_TOKEN = 'ui-acceptance-local-fixture-token'
FEATURES = ('business_goals', 'processing_episodes', 'durable_requests',
            'resume_durable_requests', 'action_context', 'minimal_brain', 'mvsc_pipeline')


def within(path, parent):
    return Path(path).resolve().is_relative_to(parent.resolve())


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def prepare():
    sandbox = Path(tempfile.mkdtemp(prefix='run-', dir=OUT)).resolve()
    for name in ('data/snapshots', 'logs', 'tmp'):
        (sandbox/name).mkdir(parents=True)
    shutil.copytree(ROOT/'ui/web', sandbox/'ui/web')
    copied = {p.relative_to(sandbox/'ui/web').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (sandbox/'ui/web').rglob('*') if p.is_file()}
    for path, expected in copied.items():
        assert hashlib.sha256((ROOT/'ui/web'/path).read_bytes()).hexdigest() == expected
    # Preserve only OS/runtime essentials; do not read or copy inherited secrets/config.
    inherited = {k:v for k,v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC'}}
    os.environ.clear()
    os.environ.update(inherited)
    os.environ.update({
        'TEMP': str(sandbox/'tmp'), 'TMP': str(sandbox/'tmp'),
        'USERPROFILE': str(sandbox),
        'PYTHON_DOTENV_DISABLED': '1', 'EVA_ENV': 'test',
        'EVA_APP_NAME': 'EVA · 隔离 API 联调 / MockLLM',
        'EVA_BASE_DIR': str(sandbox), 'EVA_DATA_DIR': str(sandbox/'data'),
        'EVA_DB_PATH': str(sandbox/'data/eva.db'), 'EVA_LOG_DIR': str(sandbox/'logs'),
        'EVA_UI_DIR': str(sandbox/'ui/web'), 'EVA_TEMPLATE_DIR': str(sandbox/'ui/web/templates'),
        'EVA_STATIC_DIR': str(sandbox/'ui/web/static'),
        'EVA_SNAPSHOT_DIR': str(sandbox/'data/snapshots'),
        'EVA_LATEST_SNAPSHOT_PATH': str(sandbox/'data/snapshots/latest.json'),
        'EVA_PROFILE_PATH': str(sandbox/'data/profile.json'),
        'EVA_SELF_MODEL_PATH': str(sandbox/'data/self_model.json'),
        'EVA_VECTOR_INDEX_PATH': str(sandbox/'data/vector_index.faiss'),
        'EVA_EMBEDDING_PROVIDER': 'none', 'EVA_STORAGE_BACKEND': 'sqlite',
        'EVA_LLM_PROVIDER': 'mock', 'EVA_API_TOKEN': FIXTURE_TOKEN,
        'EVA_ENABLE_CODE_TOOL': 'false', 'EVA_ENABLE_NETWORK_TOOLS': 'false',
        'EVA_SCHEDULER_TICK_INTERVAL_SEC': '3600',
        'EVA_SCHEDULER_MAINTENANCE_INTERVAL_SEC': '3600',
        'EVA_SCHEDULER_SNAPSHOT_INTERVAL_SEC': '3600',
        'EVA_REQUEST_TIMEOUT_SEC': '8', 'EVA_RESULT_PENDING_TIMEOUT_SEC': '30',
        'EVA_LOG_LEVEL': 'WARNING',
        **{'EVA_ENABLE_'+feature.upper(): 'false' for feature in FEATURES if feature!='resume_durable_requests'},
        'EVA_RESUME_DURABLE_REQUESTS': 'false',
    })
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    tempfile.tempdir = str(sandbox/'tmp')
    os.chdir(sandbox)
    try:
        import dotenv
        dotenv.load_dotenv = lambda *args, **kwargs: False
    except ImportError:
        pass
    blocked = []

    def deny(event, args):
        if event == 'socket.connect':
            address = args[1]
            if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
                blocked.append(event)
                raise PermissionError('Acceptance service allows only loopback connections')
        elif event == 'socket.getaddrinfo':
            if args[0] not in ('127.0.0.1', '::1', 'localhost', None):
                blocked.append(event)
                raise PermissionError('External DNS is disabled in acceptance service')
        elif event == 'sqlite3.connect' and args[0] != ':memory:':
            database = os.fsdecode(args[0])
            if database.startswith('file:'):
                uri = urlsplit(database)
                if uri.netloc not in ('', 'localhost'):
                    raise PermissionError('Nonlocal SQLite URI is disabled')
                database = url2pathname(uri.path)
            if not within(database, sandbox):
                blocked.append(event)
                raise PermissionError('Database must be in this acceptance sandbox')
        elif event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            mode, flags = args[1:3]
            writing = (isinstance(mode, str) and any(c in mode for c in 'wa+')) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and not within(os.fsdecode(args[0]), sandbox):
                blocked.append(event)
                raise PermissionError('File writes must stay in this acceptance sandbox')
    sys.addaudithook(deny)
    return sandbox, copied, blocked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8784)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    started = datetime.now(timezone.utc).isoformat()
    sandbox, copied, blocked = prepare()
    try:
        from app.main import app
        from app.config import settings
        from core.llm_adapter import get_llm, MockLLM
        from fastapi import Request
        from fastapi.testclient import TestClient
    except Exception as exc:
        report=sandbox/'startup-failure.json'
        write(report, {'status':'failed', 'startedAt':started,
                       'finishedAt':datetime.now(timezone.utc).isoformat(),
                       'phase':'app_import', 'type':type(exc).__name__, 'message':str(exc)})
        print(json.dumps({'status':'failed','report':str(report)},ensure_ascii=False))
        raise

    assert isinstance(get_llm(), MockLLM)
    paths = {name:str(getattr(settings, name)) for name in (
        'base_dir','data_dir','db_path','log_dir','ui_dir','template_dir','static_dir',
        'snapshot_dir','latest_snapshot_path','profile_path','self_model_path','vector_index_path')}
    assert all(within(p, sandbox) for p in paths.values())
    feature_flags = {feature:getattr(settings, 'enable_'+feature) for feature in FEATURES if feature!='resume_durable_requests'}
    feature_flags['resume_durable_requests'] = settings.resume_durable_requests
    assert not any(feature_flags.values())
    environment = dict(schemaVersion=1, taskId='G2-01', candidateId=OUT.parent.name,
        createdAt=started, sandbox=str(sandbox), paths=paths, featureFlags=feature_flags,
        harnessSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        service='actual app.main bootstrap and API routes', model='MockLLM; no remote model calls',
        dataSource='fresh SQLite; no production copy', templates='byte-identical copies of current product UI',
        templateHashes=copied, token='fixed local test fixture, not a production credential',
        authentication='actual AuthMiddleware; clients use X-API-Token',
        writes='isolated database, snapshots, logs, profile/persona and acceptance evidence inside sandbox',
        network='loopback only; Python audit guard, not an OS sandbox',
        config='inherited EVA/provider/proxy config cleared; dotenv loading disabled before app import',
        limits='feature-on branches, real model/provider, Cloudflare login, browser rendering and device tests not covered')
    write(sandbox/'environment.json', environment)

    @app.get('/__ui_acceptance__/environment')
    def acceptance_environment(request: Request):
        return {'acceptanceOnly': True, 'sandbox': str(sandbox), 'model': 'mock',
                'featureFlags': feature_flags, 'ready': request.app.state.container.system_state.get('ready')}

    if args.serve:
        print(json.dumps({'mode':'serve','url':f'http://127.0.0.1:{args.port}',
                          'environment':str(sandbox/'environment.json')}, ensure_ascii=False), flush=True)
        import uvicorn
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        return

    checks=[]
    def check(name, passed, actual):
        checks.append({'name':name, 'passed':bool(passed), 'actual':actual})
        if not passed:
            raise AssertionError(name)
    failure=None
    try:
        with TestClient(app) as client:
            missing=client.get('/api/runtime')
            check('auth_missing', missing.status_code==401, {'status':missing.status_code})
            client.headers['X-API-Token']=FIXTURE_TOKEN
            ready=client.get('/health/ready')
            check('actual_runtime_ready', ready.status_code==200 and ready.json()['status']=='ready', ready.json())
            metadata=client.get('/__ui_acceptance__/environment')
            check('sandbox_identity', metadata.status_code==200 and metadata.json()['sandbox']==str(sandbox), metadata.json())
            for path in ('/', '/chat', '/settings', '/memory', '/memory/list', '/mvsc'):
                response=client.get(path)
                check('template_'+path, response.status_code==200 and 'text/html' in response.headers['content-type'], {'status':response.status_code, 'bytes':len(response.content)})
            modes=client.get('/api/chat/modes')
            check('actual_mode_capabilities', modes.status_code==200 and all(m['strategy']=='mock' for m in modes.json()['modes']), modes.json())
            graph=client.get('/api/memory/graph')
            check('actual_graph_projection', graph.status_code==200 and 'nodes' in graph.json(), {'status':graph.status_code, 'nodes':len(graph.json().get('nodes', []))})
            disabled=client.get('/api/goals/graph')
            check('goals_disabled_contract', disabled.status_code==503, {'status':disabled.status_code, 'body':disabled.json()})
            act=client.get('/api/action-context/not-created')
            check('action_context_disabled_contract', act.status_code==503, {'status':act.status_code,'body':act.json()})
            receipt=client.post('/api/chat/sync',json={'text':'隔离联调准备检查','mode':'normal','remember':False})
            data=receipt.json()
            check('pipeline_smoke_normal', receipt.status_code==200 and data.get('mode')=='normal' and data.get('ok') is True,
                  {'status':receipt.status_code,'receipt':data})
            task_id=data['task_id']
            query=client.get('/api/chat/result/'+task_id)
            check('same_retained_receipt', query.status_code==200 and query.json().get('task_id')==task_id, {'status':query.status_code, 'task_id':query.json().get('task_id')})
            too_long=client.post('/api/chat',json={'text':'界'*4001,'mode':'normal'})
            check('server_4001_rejected', too_long.status_code==422, {'status':too_long.status_code})
            with client.websocket_connect('/ws',headers={'X-API-Token':FIXTURE_TOKEN}) as ws:
                ws.send_text('ping')
                pong=ws.receive_json()
                check('actual_websocket_ping', pong.get('pong') is True, {'pong':pong.get('pong')})
            check('database_isolated', Path(app.state.container.store.db_path).resolve()==Path(settings.db_path).resolve(), {'db_path':str(app.state.container.store.db_path)})
    except Exception as exc:
        failure={'type':type(exc).__name__, 'message':str(exc)}
    result=dict(taskId='G2-01', candidateId=OUT.parent.name, startedAt=started,
        finishedAt=datetime.now(timezone.utc).isoformat(), status='failed' if failure else 'passed',
        environmentPath=str(sandbox/'environment.json'), checks=checks,
        blockedAuditEvents=blocked, failure=failure,
        scope='actual API/bootstrap readiness in TestClient; not end-to-end browser or full G2-02 acceptance')
    write(sandbox/'readiness.json', result)
    print(json.dumps({'status':result['status'],'checks':len(checks),'environment':result['environmentPath'],
                      'report':str(sandbox/'readiness.json'),'failure':failure},ensure_ascii=False))
    if failure:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
