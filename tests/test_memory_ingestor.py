"""Unit tests for MemoryIngestor — memory record creation and scoring."""

from unittest.mock import MagicMock


from core.memory_ingestor import MemoryIngestor
from memory.memory_governor import MemoryGovernor
from memory.memory_schema import MemoryType


class TestMemoryIngestor:
    def test_ingest_creates_episodic_record(self):
        governor = MagicMock(spec=MemoryGovernor)
        ingestor = MemoryIngestor(governor, None)
        ingestor.ingest_user_message(
            "Hello world", event_id="ev1", source="user", active_tasks=[],
        )
        governor.ingest.assert_called_once()
        record, features = governor.ingest.call_args[0]
        assert record.memory_type == MemoryType.EPISODIC
        assert record.content == "Hello world"
        assert record.confidence == 0.7

    def test_no_governor_is_noop(self):
        ingestor = MemoryIngestor(None, None)
        # Should not raise
        ingestor.ingest_user_message(
            "Hello", event_id="ev1", source="user", active_tasks=[],
        )

    def test_user_source_high_reliability(self):
        governor = MagicMock(spec=MemoryGovernor)
        ingestor = MemoryIngestor(governor, None)
        ingestor.ingest_user_message(
            "Hello", event_id="ev1", source="user", active_tasks=[],
        )
        _, features = governor.ingest.call_args[0]
        assert features.source_reliability == 0.9

    def test_non_user_source_lower_reliability(self):
        governor = MagicMock(spec=MemoryGovernor)
        ingestor = MemoryIngestor(governor, None)
        ingestor.ingest_user_message(
            "Hello", event_id="ev1", source="github", active_tasks=[],
        )
        _, features = governor.ingest.call_args[0]
        assert features.source_reliability == 0.6

    def test_command_prefix_sets_user_explicit(self):
        governor = MagicMock(spec=MemoryGovernor)
        ingestor = MemoryIngestor(governor, None)
        ingestor.ingest_user_message(
            "/search logs", event_id="ev1", source="user", active_tasks=[],
        )
        _, features = governor.ingest.call_args[0]
        assert features.user_explicit is True

    def test_self_model_delta_and_prediction_error_passed_through(self):
        governor = MagicMock(spec=MemoryGovernor)
        ingestor = MemoryIngestor(governor, None)
        ingestor.ingest_user_message(
            "Hello", event_id="ev1", source="user", active_tasks=[],
            self_model_delta=0.5, prediction_error=0.3,
        )
        _, features = governor.ingest.call_args[0]
        assert features.self_model_delta == 0.5
        assert features.prediction_error == 0.3


class TestMemoryIngestorHelpers:
    def test_text_matches_active_task_by_description(self):
        ingestor = MemoryIngestor(None, None)
        tasks = [{"description": "Fix login bug", "name": ""}]
        assert ingestor._text_matches_active_tasks("I'm working on the fix login bug", tasks) is True

    def test_text_matches_active_task_by_name(self):
        ingestor = MemoryIngestor(None, None)
        tasks = [{"name": "RefactorModule", "description": ""}]
        assert ingestor._text_matches_active_tasks("let's refactormodule today", tasks) is True

    def test_text_no_match_empty_tasks(self):
        ingestor = MemoryIngestor(None, None)
        assert ingestor._text_matches_active_tasks("something", []) is False

    def test_text_no_match_unrelated(self):
        ingestor = MemoryIngestor(None, None)
        tasks = [{"description": "Fix login bug"}]
        assert ingestor._text_matches_active_tasks("deploy new feature", tasks) is False

    def test_text_matches_persona_by_capability(self):
        self_model = {"capabilities": ["chat", "memory_write"]}
        ingestor = MemoryIngestor(None, self_model)
        assert ingestor._text_matches_persona("can you use memory_write for this?") is True

    def test_text_matches_persona_by_identity(self):
        self_model = {"identity": "EVA", "capabilities": []}
        ingestor = MemoryIngestor(None, self_model)
        assert ingestor._text_matches_persona("hey eva what's up?") is True

    def test_text_no_persona_match(self):
        self_model = {"identity": "EVA", "capabilities": ["chat"]}
        ingestor = MemoryIngestor(None, self_model)
        assert ingestor._text_matches_persona("random text") is False

    def test_text_no_persona_when_no_self_model(self):
        ingestor = MemoryIngestor(None, None)
        assert ingestor._text_matches_persona("use chat") is False


class TestEmotionalIntensity:
    def test_high_intensity_markers(self):
        for marker in ["urgent", "紧急", "asap", "!!!", "critical", "严重"]:
            assert MemoryIngestor._estimate_emotional_intensity(f"this is {marker}") == 0.8

    def test_medium_intensity_markers(self):
        for marker in ["worried", "担心", "frustrated", "important", "重要"]:
            assert MemoryIngestor._estimate_emotional_intensity(f"this is {marker}") == 0.4

    def test_default_low_intensity(self):
        assert MemoryIngestor._estimate_emotional_intensity("hello world") == 0.1
