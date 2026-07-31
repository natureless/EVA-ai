"""Unit tests for TieredMemory — S1, S2, S3, stats, ingest."""

from unittest.mock import MagicMock

import pytest

from memory.tiered_store import (
    SessionMemory,
    WorkingMemoryStore,
    LongTermMemoryStore,
    TieredMemoryManager,
)


class TestSessionMemory:
    def test_put_and_get(self):
        s1 = SessionMemory(max_entries=10)
        s1.put("k1", {"content": "hello"})
        assert s1.get("k1") == {"content": "hello"}

    def test_get_missing_returns_none(self):
        s1 = SessionMemory()
        assert s1.get("nonexistent") is None

    def test_evict_expired(self):
        s1 = SessionMemory(ttl_seconds=-1)
        s1.put("k1", {"content": "hello"})
        assert s1.evict_expired() == 1
        assert s1.get("k1") is None

    def test_stats(self):
        s1 = SessionMemory()
        s1.put("k1", {"content": "hello"})
        stats = s1.stats()
        assert stats["entries"] == 1
        assert stats["max_entries"] == 200


class TestWorkingMemoryStore:
    def test_put_and_get(self):
        store = MagicMock()
        store.fetchone.return_value = {
            "id": "m1", "content": "test", "summary": "", "source": "",
            "tags_json": "[]", "salience": 0.5, "created_at": "2026-01-01T00:00:00",
        }
        wm = WorkingMemoryStore(store)
        result = wm.get("m1")
        assert result is not None
        assert result["content"] == "test"

    def test_list_recent_empty(self):
        store = MagicMock()
        store.fetchall.return_value = []
        wm = WorkingMemoryStore(store)
        items = wm.list_recent()
        assert items == []

    def test_stats(self):
        store = MagicMock()
        store.fetchall.return_value = [{"cnt": 5}]
        wm = WorkingMemoryStore(store)
        stats = wm.stats()
        assert stats["entries"] == 5
        assert stats["tier"] == "S2_working"


class TestLongTermMemoryStore:
    def test_get(self):
        store = MagicMock()
        store.fetchone.return_value = {
            "id": "m1", "content": "ltm test", "summary": "", "category": "general",
            "importance": 0.8, "source_event_id": "ev1",
            "tags_json": "[]", "created_at": "2026-01-01T00:00:00",
        }
        ltm = LongTermMemoryStore(store)
        result = ltm.get("m1")
        assert result is not None
        assert result["content"] == "ltm test"

    def test_list_recent_empty(self):
        store = MagicMock()
        store.fetchall.return_value = []
        ltm = LongTermMemoryStore(store)
        items = ltm.list_recent()
        assert items == []

    def test_stats(self):
        store = MagicMock()
        store.fetchall.side_effect = [[{"cnt": 10}], [{"cnt": 3}]]
        ltm = LongTermMemoryStore(store)
        stats = ltm.stats()
        assert stats["entries_active"] == 10
        assert stats["entries_archived"] == 3


class TestTieredMemoryManager:
    def test_stats(self):
        tm = TieredMemoryManager(MagicMock())
        stats = tm.stats()
        assert "S1_session" in stats
        assert "S2_working" in stats
        assert "S3_long_term" in stats

    def test_recall_empty(self):
        tm = TieredMemoryManager(MagicMock())
        results = tm.recall("test query")
        assert isinstance(results, list)
