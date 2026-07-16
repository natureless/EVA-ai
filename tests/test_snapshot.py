import importlib
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_snapshot_save_endpoint(client):
    client.post("/api/chat", json={"text": "Update EVA persona"})
    response = client.post("/api/debug/snapshot/save")
    assert response.status_code == 200
    assert response.json()["ok"] is True

    snapshot = client.get("/api/debug/snapshot")
    assert snapshot.status_code == 200
    payload = snapshot.json()["snapshot"]

    assert payload is not None
    assert payload["world_model"]["focus"].startswith("Update EVA persona")
    assert "proactive_state" in payload


def test_snapshot_restore(tmp_path, monkeypatch):
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

    for mod in ["app.config", "app.bootstrap", "app.api", "app.main"]:
        if mod in sys.modules:
            del sys.modules[mod]

    import app.main

    importlib.reload(app.main)

    with TestClient(app.main.app) as client_one:
        client_one.post("/api/chat", json={"text": "Restore focus test"})
        time.sleep(1.2)
        client_one.post("/api/debug/maintenance/trigger")
        time.sleep(0.3)
        client_one.post("/api/debug/snapshot/save")

    for mod in ["app.config", "app.bootstrap", "app.api", "app.main"]:
        if mod in sys.modules:
            del sys.modules[mod]

    import app.main as app_main_second

    importlib.reload(app_main_second)

    with TestClient(app_main_second.app) as client_two:
        state = client_two.get("/api/state").json()
        assert "Restore focus test" in state["focus"]
        assert state["last_reply"] != ""
        proactive = client_two.get("/api/proactive/state").json()
        assert proactive["proactive_state"].get("last_user_message_ts") is not None
