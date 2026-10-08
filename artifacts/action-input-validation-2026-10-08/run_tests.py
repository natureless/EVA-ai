"""Reproduce isolated validation; policy/source roots retain their normal meaning."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    args = sys.argv[1:] or ["-q"]
    with tempfile.TemporaryDirectory(prefix="eva-input-validation-") as temporary:
        base = Path(temporary)
        env = {k:v for k,v in os.environ.items() if not k.startswith("EVA_") and k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")}
        env.update(PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", PYTHONPATH=str(ROOT), EVA_LLM_PROVIDER="mock", EVA_EMBEDDING_PROVIDER="none", EVA_ENABLE_ACTION_CONTEXT="false", EVA_ENABLE_DURABLE_REQUESTS="false", EVA_ENABLE_PROCESSING_EPISODES="false", EVA_ENABLE_BUSINESS_GOALS="false", EVA_ENABLE_MINIMAL_BRAIN="false", EVA_ENABLE_MVSC_PIPELINE="false", EVA_ENABLE_CODE_TOOL="false", EVA_ENABLE_NETWORK_TOOLS="false", EVA_ENABLE_US_MARKET_SNAPSHOT="false", EVA_GITHUB_POLL_REPOS="")
        for key, relative in {"DATA_DIR":"data", "LOG_DIR":"logs", "DB_PATH":"data/eva.db", "SNAPSHOT_DIR":"data/snapshots", "LATEST_SNAPSHOT_PATH":"data/snapshots/latest.json", "PROFILE_PATH":"data/profile.json", "SELF_MODEL_PATH":"data/self_model.json", "VECTOR_INDEX_PATH":"data/vector_index.faiss"}.items():
            env["EVA_"+key] = str(base / relative)
        runner = base / "pytest_runner.py"
        runner.write_text("import dotenv\ndotenv.load_dotenv=lambda *a, **k: False\nif __name__ == '__main__':\n import pytest,sys\n raise SystemExit(pytest.main(sys.argv[1:]))\n", encoding="utf-8")
        started = time.monotonic()
        output = ROOT / "artifacts/action-input-final-python-2026-10-08.log"
        with output.open("w", encoding="utf-8") as log:
            proc = subprocess.run([sys.executable, str(runner), *args], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        result = {"date":"2026-10-08", "python_exit_code":proc.returncode, "elapsed_seconds":round(time.monotonic()-started,2), "isolation":"temporary storage; normal policy/source root; dotenv disabled; mock model; guarded multiprocessing entry point", "production_feature_flags_changed":False, "browser_visual_acceptance":False}
        (ROOT / "artifacts/action-input-final-2026-10-08.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(result))
        print("\n".join(output.read_text(encoding="utf-8", errors="replace").splitlines()[-35:]))
        raise SystemExit(proc.returncode)
