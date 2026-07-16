import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    ui_dir = tmp_path / "ui" / "web"
    template_dir = ui_dir / "templates"
    static_dir = ui_dir / "static"
    snapshot_dir = data_dir / "snapshots"

    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    template_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    (template_dir / "dashboard.html").write_text(
        "<html><body><h1>{{ app_name }}</h1></body></html>",
        encoding="utf-8",
    )
    (static_dir / "app.js").write_text("console.log('test');", encoding="utf-8")
    (static_dir / "styles.css").write_text("body{}", encoding="utf-8")

    monkeypatch.setenv("EVA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EVA_LOG_DIR", str(logs_dir))
    monkeypatch.setenv("EVA_DB_PATH", str(data_dir / "eva.db"))
    monkeypatch.setenv("EVA_UI_DIR", str(ui_dir))
    monkeypatch.setenv("EVA_TEMPLATE_DIR", str(template_dir))
    monkeypatch.setenv("EVA_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("EVA_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("EVA_LATEST_SNAPSHOT_PATH", str(snapshot_dir / "latest.json"))
    monkeypatch.setenv("EVA_PROFILE_PATH", str(data_dir / "profile.json"))
    monkeypatch.setenv("EVA_PERSONA_PATH", str(data_dir / "persona.json"))
    monkeypatch.setenv("EVA_SELF_MODEL_PATH", str(data_dir / "self_model.json"))

    monkeypatch.setenv("EVA_STAGNATION_THRESHOLD_SEC", "1")
    monkeypatch.setenv("EVA_REMINDER_COOLDOWN_SEC", "2")
    monkeypatch.setenv("EVA_SCHEDULER_TICK_INTERVAL_SEC", "60")
    monkeypatch.setenv("EVA_SCHEDULER_MAINTENANCE_INTERVAL_SEC", "60")
    monkeypatch.setenv("EVA_SCHEDULER_SNAPSHOT_INTERVAL_SEC", "60")
    monkeypatch.setenv("EVA_REQUEST_TIMEOUT_SEC", "5")
    monkeypatch.setenv("EVA_RESULT_TTL_SEC", "10")

    for mod in [
        "app.config",
        "app.bootstrap",
        "app.api",
        "app.main",
    ]:
        if mod in sys.modules:
            del sys.modules[mod]

    import app.main

    importlib.reload(app.main)

    with TestClient(app.main.app) as test_client:
        yield test_client
