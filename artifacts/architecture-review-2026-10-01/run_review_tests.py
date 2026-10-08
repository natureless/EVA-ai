import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(r'D:\EVA-ai')
OUTPUT = ROOT / 'artifacts' / 'architecture-review-2026-10-01'
GUARDS = [ROOT / name for name in ('data/eva.db', 'data/snapshots/latest.json', 'data/profile.json', 'data/self_model.json', 'data/mvsc_event_store.db')]

def fingerprints():
    return {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in GUARDS}

if __name__ == '__main__':
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = fingerprints()
    with tempfile.TemporaryDirectory(prefix='eva-review-tests-') as temporary:
        base = Path(temporary)
        environment = {k: v for k, v in os.environ.items() if not k.startswith('EVA_') and k not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY')}
        environment.update(PYTHONUTF8='1', PYTHON_DOTENV_DISABLED='1', EVA_LLM_PROVIDER='mock', EVA_EMBEDDING_PROVIDER='none', EVA_ENABLE_MVSC_PIPELINE='false', EVA_ENABLE_MINIMAL_BRAIN='false', EVA_ENABLE_BUSINESS_GOALS='false', EVA_ENABLE_PROCESSING_EPISODES='false', EVA_ENABLE_DURABLE_REQUESTS='false', EVA_ENABLE_CODE_TOOL='false', EVA_ENABLE_NETWORK_TOOLS='false', EVA_ENABLE_US_MARKET_SNAPSHOT='false', EVA_GITHUB_POLL_REPOS='')
        for field, relative in {'DATA_DIR':'data','LOG_DIR':'logs','DB_PATH':'data/eva.db','SNAPSHOT_DIR':'data/snapshots','LATEST_SNAPSHOT_PATH':'data/snapshots/latest.json','PROFILE_PATH':'data/profile.json','SELF_MODEL_PATH':'data/self_model.json','VECTOR_INDEX_PATH':'data/vector_index.faiss'}.items():
            environment['EVA_'+field] = str(base / relative)
        runner = base / 'run_review_tests.py'
        runner.write_text("import dotenv\ndotenv.load_dotenv = lambda *args, **kwargs: False\nif __name__ == '__main__':\n    import sys\n    import pytest\n    raise SystemExit(pytest.main(sys.argv[1:]))\n", encoding='utf-8')
        environment['PYTHONPATH'] = str(ROOT)
        command = [sys.executable, str(runner), '-q', '--junitxml='+str(OUTPUT/'pytest.xml')]
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        (OUTPUT / 'pytest.txt').write_text(completed.stdout, encoding='utf-8')
        after = fingerprints()
        result = {'date':'2026-10-01','python':sys.version.split()[0],'exit_code':completed.returncode,'elapsed_sec':round(time.perf_counter()-started,2),'isolation':'temporary configured storage; MockLLM; dotenv disabled; optional features disabled unless test enables them','protected_default_files_unchanged':before == after,'changed_protected_paths':[key for key in before if before[key] != after[key]],'output':'artifacts/architecture-review-2026-10-01/pytest.txt','junit':'artifacts/architecture-review-2026-10-01/pytest.xml'}
        (OUTPUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        print('\n'.join(completed.stdout.splitlines()[-18:]))
        raise SystemExit(completed.returncode)
