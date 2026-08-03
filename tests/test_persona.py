"""Unit tests for persona module — models, repository, service, self_model."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from persona.models import PersonaProfile
from persona.repository import PersonaRepository
from persona.service import PersonaService
from persona.self_model_store import SelfModelStore


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Ensure each test runs in its own temp dir to avoid cwd pollution."""
    monkeypatch.chdir(tmp_path)


class TestPersonaProfile:
    def test_create_default_profile(self):
        profile = PersonaProfile(
            persona_id="test-1",
            name="EVA",
            role_definition="assistant",
            tone_style="calm",
            version=1,
            updated_at=datetime.now(timezone.utc),
        )
        assert profile.name == "EVA"
        assert profile.version == 1

    def test_to_record_includes_all_fields(self):
        profile = PersonaProfile(
            persona_id="test-2",
            name="Test",
            role_definition="test role",
            tone_style="precise",
            hard_constraints=["no lies"],
            soft_preferences=["be concise"],
            value_weights={"truth": 1.0},
            version=2,
            updated_at=datetime.now(timezone.utc),
            confidence=0.9,
        )
        record = profile.to_record()
        assert record["name"] == "Test"
        assert record["hard_constraints"] == ["no lies"]
        assert record["value_weights"] == {"truth": 1.0}


class TestPersonaRepository:
    def test_get_active_returns_none_when_empty(self):
        store = MagicMock()
        store.fetchone.return_value = None
        repo = PersonaRepository(store)
        assert repo.get_active() is None

    def test_get_active_returns_profile(self):
        store = MagicMock()
        store.fetchone.return_value = {
            "persona_id": "p1",
            "name": "EVA",
            "role_definition": "assistant",
            "tone_style": "calm",
            "hard_constraints": '["no lies"]',
            "soft_preferences": '["be concise"]',
            "value_weights": '{"truth": 1.0}',
            "version": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "confidence": 0.9,
            "source_event_id": None,
        }
        store.loads_json.side_effect = lambda s: json.loads(s)
        repo = PersonaRepository(store)
        profile = repo.get_active()
        assert profile is not None
        assert profile.name == "EVA"

    def test_upsert_calls_store(self):
        store = MagicMock()
        store.dumps_json.side_effect = lambda obj: json.dumps(obj)
        repo = PersonaRepository(store)
        profile = PersonaProfile(
            persona_id="p1",
            name="EVA",
            role_definition="assistant",
            tone_style="calm",
            version=1,
            updated_at=datetime.now(timezone.utc),
        )
        repo.upsert(profile)
        store.execute.assert_called_once()


class TestPersonaService:
    def test_get_active_returns_existing(self):
        repo = MagicMock(spec=PersonaRepository)
        existing = PersonaProfile(
            persona_id="p1", name="HAL", role_definition="spaceship AI",
            tone_style="monotone", version=1,
            updated_at=datetime.now(timezone.utc),
        )
        repo.get_active.return_value = existing
        svc = PersonaService(repo)
        result = svc.get_active_persona()
        assert result.name == "HAL"

    def test_get_active_creates_default_when_none(self):
        repo = MagicMock(spec=PersonaRepository)
        repo.get_active.return_value = None
        svc = PersonaService(repo)
        result = svc.get_active_persona()
        assert result.name == "EVA"
        repo.upsert.assert_called_once()

    def test_update_profile_increments_version(self):
        repo = MagicMock(spec=PersonaRepository)
        existing = PersonaProfile(
            persona_id="p1", name="EVA", role_definition="assistant",
            tone_style="calm", version=1,
            updated_at=datetime.now(timezone.utc),
        )
        repo.get_active.return_value = existing
        svc = PersonaService(repo)
        updated = svc.update_profile({"name": "EVA v2"})
        assert updated.name == "EVA v2"
        assert updated.version == 2

    def test_render_system_prompt(self):
        repo = MagicMock(spec=PersonaRepository)
        svc = PersonaService(repo)
        persona = PersonaProfile(
            persona_id="p1", name="Test", role_definition="tester",
            tone_style="direct", hard_constraints=["no lies"],
            soft_preferences=["be concise"], version=1,
            updated_at=datetime.now(timezone.utc),
        )
        prompt = svc.render_system_prompt({"persona": persona})
        assert "Name: Test" in prompt
        assert "no lies" in prompt

    def test_validate_response_no_violations(self):
        repo = MagicMock(spec=PersonaRepository)
        svc = PersonaService(repo)
        violations = svc.validate_response("I think this might work.")
        assert violations == []

    def test_validate_response_overclaiming(self):
        repo = MagicMock(spec=PersonaRepository)
        svc = PersonaService(repo)
        violations = svc.validate_response("I am 100% certain this is correct.")
        assert len(violations) >= 1
        assert "overclaiming" in violations[0]


class TestSelfModelStore:
    def test_load_or_init_creates_default(self, tmp_path):
        path = tmp_path / "init_test" / "self_model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SelfModelStore(path)
        model = store.load_or_init()
        assert model["identity"] == "EVA v0.1"
        assert model["_version"] == 2

    def test_record_state_change(self, tmp_path):
        path = tmp_path / "state_test" / "self_model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SelfModelStore(path)
        model = store.load_or_init()
        updated = store.record_state_change(
            model, change_type="test", detail="unit test", focus="testing",
        )
        assert len(updated["state_history"]) == 1

    def test_record_prediction_error(self, tmp_path):
        path = tmp_path / "pred_error_test" / "self_model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SelfModelStore(path)
        model = store.load_or_init()
        updated = store.record_prediction_error(model, error=0.5)
        metrics = updated["stability_metrics"]
        assert metrics["mean_prediction_error"] == pytest.approx(0.5)

    def test_record_perturbation(self, tmp_path):
        path = tmp_path / "perturb_test" / "self_model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SelfModelStore(path)
        model = store.load_or_init()
        updated = store.record_perturbation(
            model, cause="test", delta_magnitude=0.3,
            affected_fields=["focus"],
        )
        metrics = updated["stability_metrics"]
        assert metrics["total_perturbations"] == 1
        assert metrics["stability_score"] < 1.0

    def test_state_history_capped(self, tmp_path):
        path = tmp_path / "cap_test" / "self_model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SelfModelStore(path)
        model = store.load_or_init()
        for i in range(60):
            model = store.record_state_change(
                model, change_type="test", detail=f"entry {i}",
            )
        assert len(model["state_history"]) <= 50
