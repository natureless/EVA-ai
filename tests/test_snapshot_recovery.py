"""Recovery preserves the complete snapshot schema used by normal persistence."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_routes import routes_health
from app.bootstrap import _snapshot_callback
from persona.models import PersonaProfile
from runtime.diagnostics import RecoveryActions
from world.snapshot_store import SnapshotStore
from world.world_model import WorldModelGraph


def test_http_rebuild_uses_runtime_serializer_and_keeps_all_subsystems(tmp_path, monkeypatch):
    path = tmp_path / "snapshots" / "latest.json"
    store = SnapshotStore(path.parent, path)
    world_model = WorldModelGraph(focus="current-focus")
    persona = PersonaProfile(
        persona_id="test", name="EVA", role_definition="assistant", tone_style="plain",
        version=1, updated_at=datetime.now(timezone.utc),
    )
    state = {"minimal_brain": {"schema_version": 1, "memory": [{"summary": "feedback"}]}}
    memory = SimpleNamespace(
        s1=SimpleNamespace(list_all=lambda: [("session", {"summary": "working"})]),
        s2=SimpleNamespace(list_recent=lambda **kwargs: [{"id": "w1", "content": "recent"}]),
        s3=SimpleNamespace(search=lambda *args, **kwargs: [{"id": "l1", "content": "retained"}]),
    )
    save = _snapshot_callback(
        state=state,
        world=SimpleNamespace(model=world_model, snapshot_store=store, proactive_state={"reason": "idle"}),
        identity=SimpleNamespace(profile={"name": "EVA"}, self_model={"identity": "assistant"}),
        persona_service=SimpleNamespace(get_active_persona=lambda: persona),
        tiered_memory=memory,
    )
    store.save_latest({"version": "broken", "world_model": {}})
    monkeypatch.setattr(routes_health.settings, "latest_snapshot_path", path)
    app = FastAPI()
    app.state.container = SimpleNamespace(
        recovery_actions=RecoveryActions(), save_runtime_snapshot=save,
    )
    app.include_router(routes_health.router)
    with TestClient(app) as client:
        response = client.post("/health/recover", json={"action": "rebuild_snapshot"})
    assert response.json()["ok"] is True
    restored = store.load_latest()
    assert restored["world_model"]["focus"] == "current-focus"
    assert restored["profile"] == {"name": "EVA"}
    assert restored["self_model"] == {"identity": "assistant"}
    assert restored["persona"]["persona_id"] == "test"
    assert restored["memory"]["s1_session"][0]["key"] == "session"
    assert restored["proactive_state"] == {"reason": "idle"}
    assert restored["minimal_brain"] == state["minimal_brain"]
    assert state["last_snapshot_at"]


def test_legacy_world_only_recovery_preserves_existing_extra_fields(tmp_path):
    path = tmp_path / "latest.json"
    original = {"version": "2", "world_model": {}, "persona": {"name": "EVA"}, "memory": {"s1": []}}
    path.write_text(json.dumps(original), encoding="utf-8")
    result = RecoveryActions.recover_snapshot(path, WorldModelGraph(focus="updated"))
    assert result.passed
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["world_model"]["focus"] == "updated"
    assert restored["persona"] == original["persona"]
    assert restored["memory"] == original["memory"]
    assert restored["version"] == "2"


def test_failed_runtime_snapshot_recovery_does_not_overwrite_with_partial_fallback(tmp_path):
    path = tmp_path / "latest.json"
    original = '{"world_model":{},"persona":{"name":"EVA"}}'
    path.write_text(original, encoding="utf-8")

    def failing_save():
        raise OSError("simulated write failure")

    result = RecoveryActions.recover_snapshot(path, save_snapshot=failing_save)
    assert result.passed is False
    assert "simulated write failure" in result.detail
    assert path.read_text(encoding="utf-8") == original
