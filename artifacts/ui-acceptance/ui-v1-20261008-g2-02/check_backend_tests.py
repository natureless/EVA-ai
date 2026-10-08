"""Run related existing backend tests with .env and production paths disabled."""
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode=True
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
spec=importlib.util.spec_from_file_location('isolator',ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py')
isolator=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolator)
isolator.OUT=OUT
sandbox,copied,blocked=isolator.prepare()
import pytest
status=pytest.main([str(ROOT/'tests/test_chat_modes.py'),str(ROOT/'tests/test_request_receipts.py'),
    '-q','--capture=sys','--log-file',str(sandbox/'pytest.log'),
    '--basetemp',str(sandbox/'pytest-data'),'-o','cache_dir='+str(sandbox/'pytest-cache')])
isolator.write(sandbox/'backend-tests.json',{'exitCode':int(status),'blockedAuditEvents':blocked,
    'scope':'existing chat mode and receipt tests; MockLLM, isolated paths, no dotenv',
    'sandbox':str(sandbox)})
print('Backend isolation report: '+str(sandbox/'backend-tests.json'))
raise SystemExit(status)
