"""Unit tests for MemoryGovernor — ingest, access, maintenance."""

from unittest.mock import MagicMock


from memory.memory_governor import MemoryGovernor, MemoryRepository
from memory.memory_schema import MemoryRecord, MemoryType
from memory.importance_scorer import ImportanceFeatures


def _make_record(content: str = "test", salience: float = 0.5,
                 conflict_keys: list | None = None) -> MemoryRecord:
    return MemoryRecord(
        id="mem_001",
        memory_type=MemoryType.EPISODIC,
        content=content,
        source_event_id="ev_001",
        salience=salience,
        confidence=0.7,
        ttl_seconds=None,
        conflict_keys=conflict_keys or [],
    )


def _make_features(**kwargs) -> ImportanceFeatures:
    defaults = {
        "user_explicit": False,
        "goal_related": False,
        "blocker_related": False,
        "persona_related": False,
        "repeated_mentions": 0,
        "source_reliability": 0.9,
        "emotional_intensity": 0.1,
        "age_hours": 0.0,
        "self_model_delta": 0.0,
        "prediction_error": 0.0,
    }
    defaults.update(kwargs)
    return ImportanceFeatures(**defaults)


class TestMemoryGovernor:
    def test_ingest_sets_salience(self):
        repo = MagicMock(spec=MemoryRepository)
        gov = MemoryGovernor(repo)
        record = _make_record()
        features = _make_features(user_explicit=True, goal_related=True)
        result = gov.ingest(record, features)
        assert result.salience > 0.5
        repo.upsert.assert_called_once()

    def test_ingest_resolves_conflicts(self):
        repo = MagicMock(spec=MemoryRepository)
        repo.find_by_conflict_keys.return_value = [_make_record("conflict")]
        gov = MemoryGovernor(repo)
        record = _make_record(content="new info", conflict_keys=["topic_x"])
        features = _make_features()
        result = gov.ingest(record, features)
        assert result is not None
        repo.upsert.assert_called_once()
        repo.find_by_conflict_keys.assert_called_once_with(["topic_x"])

    def test_ingest_high_salience_flows_to_tiered(self):
        tiered = MagicMock()
        repo = MagicMock(spec=MemoryRepository)
        gov = MemoryGovernor(repo, tiered_memory=tiered)
        record = _make_record()
        features = _make_features(user_explicit=True, goal_related=True, emotional_intensity=0.8)
        result = gov.ingest(record, features)
        assert result.salience >= 0.6
        tiered.ingest.assert_called_once()

    def test_ingest_low_salience_skips_tiered(self):
        tiered = MagicMock()
        repo = MagicMock(spec=MemoryRepository)
        gov = MemoryGovernor(repo, tiered_memory=tiered)
        record = _make_record(salience=0.3)
        features = _make_features()
        gov.ingest(record, features)
        tiered.ingest.assert_not_called()

    def test_ingest_tiered_exception_graceful(self):
        tiered = MagicMock()
        tiered.ingest.side_effect = RuntimeError("tiered down")
        repo = MagicMock(spec=MemoryRepository)
        gov = MemoryGovernor(repo, tiered_memory=tiered)
        record = _make_record(salience=0.8)
        features = _make_features()
        result = gov.ingest(record, features)
        assert result is not None

    def test_access_updates_last_accessed(self):
        repo = MagicMock(spec=MemoryRepository)
        record = _make_record()
        repo.get.return_value = record
        gov = MemoryGovernor(repo)
        result = gov.access("mem_001")
        assert result is not None
        assert result.last_accessed_at is not None
        assert repo.upsert.call_count == 1

    def test_access_nonexistent(self):
        repo = MagicMock(spec=MemoryRepository)
        repo.get.return_value = None
        gov = MemoryGovernor(repo)
        result = gov.access("missing")
        assert result is None

    def test_maintenance_returns_stats(self):
        repo = MagicMock(spec=MemoryRepository)
        repo.list_active_memories.return_value = []
        repo.list_by_type.return_value = []
        gov = MemoryGovernor(repo)
        stats = gov.maintenance()
        assert "changed" in stats
        assert "compacted" in stats
        assert "groups" in stats
