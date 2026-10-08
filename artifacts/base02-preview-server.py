"""Disposable local preview of the real EVA app; no default data or .env."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for key in list(os.environ):
    if key.startswith("EVA_") or key in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}:
        del os.environ[key]
os.environ["EVA_LLM_PROVIDER"] = "mock"
import app.config  # noqa: E402 - environment must be set before loading configuration
import uvicorn  # noqa: E402
manifest = json.loads((ROOT / "configs/baselines/runtime-v1.json").read_text(encoding="utf-8"))
with tempfile.TemporaryDirectory(prefix="eva-base02-preview-") as temporary:
    work = Path(temporary)
    os.chdir(work)
    try:
        app.config.settings = app.config.Settings(
            **manifest["settings"], app_name="EVA BASE-02 隔离预览", base_dir=work,
            data_dir=work / "data", db_path=work / "data/eva.db", log_dir=work / "logs",
            snapshot_dir=work / "data/snapshots", latest_snapshot_path=work / "data/snapshots/latest.json",
            profile_path=work / "data/profile.json", self_model_path=work / "data/self_model.json",
            vector_index_path=work / "data/vector.faiss", ui_dir=ROOT / "ui/web",
            template_dir=ROOT / "ui/web/templates", static_dir=ROOT / "ui/web/static",
            enable_minimal_brain=False, enable_mvsc_pipeline=True,
        )
        with patch("dotenv.load_dotenv", return_value=False):
            import app.main
        uvicorn.run(app.main.app, host="127.0.0.1", port=8766, log_level="warning")
    finally:
        os.chdir(ROOT)
