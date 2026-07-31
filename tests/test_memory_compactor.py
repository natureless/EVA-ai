"""Unit tests for MemoryCompactor and MemoryDecayPolicy."""

from datetime import datetime, timedelta, timezone

import pytest

from memory.memory_compactor import MemoryCompactor, MemoryDecayPolicy
from memory.memory_schema import MemoryRecord, MemoryStatus, MemoryType


def _make_record(mem_type: MemoryType = MemoryType.EPISODIC,
                 content: str = "test",
                 salience: float = 0.5,
                 confidence: float = 0.7,
                 created_delta: timedelta | None = None,
                 last_accessed_delta: timedelta | None = None,
                 conflict_keys: list | None = None,
                 metadata: dict | None = None) -> MemoryRecord:
    now = datetime.now(timezone.utc)
    created_at = now - (created_delta or timedelta(0))
    last_accessed = now - (last_accessed_delta or timedelta(0))
    return MemoryRecord(
        id=f"mem_{content[:8]}",
        memory_type=mem_type,
        content=content,
        salience=salience,
        confidence=confidence,
        conflict_keys=conflict_keys or [],
        created_at=created_at,
        updated_at=now,
        last_accessed_at=last_accessed,
        metadata=metadata or {},
    )


class TestMemoryDecayPolicy:
    def test_working_memory_decays_after_12h(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.WORKING, created_delta=timedelta(hours=13))
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.status == MemoryStatus.FORGOTTEN

    def test_working_memory_keeps_within_12h(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.WORKING, created_delta=timedelta(hours=6))
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.status == MemoryStatus.ACTIVE

    def test_episodic_low_salience_archived_after_14d(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.EPISODIC, salience=0.3,
                           created_delta=timedelta(days=20),
                           last_accessed_delta=timedelta(days=20))
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.status == MemoryStatus.ARCHIVED

    def test_episodic_very_low_salience_forgotten_after_45d(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.EPISODIC, salience=0.1,
                           created_delta=timedelta(days=50),
                           last_accessed_delta=timedelta(days=50))
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.status == MemoryStatus.FORGOTTEN

    def test_episodic_high_salience_kept(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.EPISODIC, salience=0.8,
                           created_delta=timedelta(days=60),
                           last_accessed_delta=timedelta(days=60))
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.status == MemoryStatus.ACTIVE

    def test_semantic_updates_recency_score(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.SEMANTIC, metadata={"recency_score": 1.0})
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.metadata["recency_score"] == pytest.approx(0.98, rel=0.01)

    def test_semantic_recency_floor(self):
        policy = MemoryDecayPolicy()
        mem = _make_record(MemoryType.SEMANTIC, metadata={"recency_score": 0.3})
        result = policy.apply(mem, datetime.now(timezone.utc))
        assert result.metadata["recency_score"] >= 0.3


class TestMemoryCompactor:
    def test_group_requires_minimum_3(self):
        compactor = MemoryCompactor()
        mems = [
            _make_record(content=f"topic A item {i}")
            for i in range(2)
        ]
        groups = compactor.group_for_compaction(mems)
        assert len(groups) == 0  # only 2 items

    def test_group_with_sufficient_items(self):
        compactor = MemoryCompactor()
        mems = [
            _make_record(content=f"bug fix part {i}")
            for i in range(4)
        ]
        groups = compactor.group_for_compaction(mems)
        assert len(groups) >= 1

    def test_group_skips_non_episodic(self):
        compactor = MemoryCompactor()
        mems = [
            _make_record(MemoryType.SEMANTIC, content=f"semantic {i}")
            for i in range(5)
        ]
        groups = compactor.group_for_compaction(mems)
        assert len(groups) == 0

    def test_group_skips_non_active(self):
        compactor = MemoryCompactor()
        mems = [
            _make_record(content=f"archived item {i}")
            for i in range(5)
        ]
        for m in mems:
            m.status = MemoryStatus.ARCHIVED
        groups = compactor.group_for_compaction(mems)
        assert len(groups) == 0

    def test_compact_group_creates_semantic_record(self):
        compactor = MemoryCompactor()
        mems = [
            _make_record(content=f"login error on attempt {i}")
            for i in range(5)
        ]
        result = compactor.compact_group("login_errors", mems)
        assert result.memory_type == MemoryType.SEMANTIC
        assert "login_errors" in result.content
        assert result.metadata["compacted_from_count"] == 5

    def test_topic_key_uses_conflict_key(self):
        compactor = MemoryCompactor()
        mem = _make_record(conflict_keys=["login_bug"])
        assert compactor._topic_key(mem) == "login_bug"

    def test_topic_key_falls_back_to_type(self):
        compactor = MemoryCompactor()
        mem = _make_record(MemoryType.EPISODIC)
        assert compactor._topic_key(mem) == "episodic"
