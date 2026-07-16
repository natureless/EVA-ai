"""Tiered memory unit + integration tests."""

import time
from memory.tiered_store import (
    SessionMemory,
    WorkingMemoryStore,
    LongTermMemoryStore,
    WorldModelStore,
    EventTraceStore,
    TieredMemoryManager,
)
from memory.sqlite_store import SQLiteStore
from pathlib import Path
import tempfile
import os


# ── S1: Session Memory ─────────────────────────────────────

class TestSessionMemory:
    def test_put_and_get(self):
        sm = SessionMemory(max_entries=10, ttl_seconds=60)
        sm.put("key1", {"content": "hello"})
        assert sm.get("key1")["content"] == "hello"

    def test_eviction_on_ttl_expiry(self):
        sm = SessionMemory(max_entries=10, ttl_seconds=-1)  # immediate expiry
        sm.put("key1", {"content": "hello"})
        assert sm.get("key1") is None

    def test_capacity_enforcement(self):
        sm = SessionMemory(max_entries=2, ttl_seconds=3600)
        sm.put("a", {"n": 1})
        sm.put("b", {"n": 2})
        sm.put("c", {"n": 3})
        assert len(sm._store) == 2

    def test_stats(self):
        sm = SessionMemory(max_entries=50, ttl_seconds=60)
        sm.put("a", {"n": 1})
        s = sm.stats()
        assert s["tier"] == "S1_session"
        assert s["entries"] == 1


# ── S2 / S3 / S4: SQLite-backed stores ─────────────────────

class TestWorkingMemory:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_put_and_retrieve(self):
        wm = WorkingMemoryStore(self.store, max_entries=100, ttl_hours=72)
        mid = wm.put("test content", summary="test summary", source="test", priority=2)
        row = wm.get(mid)
        assert row is not None
        assert row["content"] == "test content"

    def test_list_recent(self):
        wm = WorkingMemoryStore(self.store, max_entries=100, ttl_hours=72)
        wm.put("content A", source="test")
        wm.put("content B", source="test")
        items = wm.list_recent(limit=10)
        assert len(items) >= 2

    def test_stats(self):
        wm = WorkingMemoryStore(self.store, max_entries=100, ttl_hours=72)
        s = wm.stats()
        assert s["tier"] == "S2_working"
        assert s["entries"] >= 0


class TestLongTermMemory:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_put_and_retrieve(self):
        ltm = LongTermMemoryStore(self.store, max_entries=100)
        mid = ltm.put("long term content", category="test", importance=0.9)
        row = ltm.get(mid)
        assert row is not None
        assert row["content"] == "long term content"
        assert row["status"] == "active"

    def test_archive(self):
        ltm = LongTermMemoryStore(self.store, max_entries=100)
        mid = ltm.put("to archive", category="test", importance=0.3)
        ltm.archive(mid)
        assert ltm.get(mid) is None

    def test_list_by_category(self):
        ltm = LongTermMemoryStore(self.store, max_entries=100)
        ltm.put("cat A", category="alpha", importance=0.8)
        ltm.put("cat B", category="beta", importance=0.7)
        items = ltm.list_by_category("alpha", limit=10)
        assert all(i["category"] == "alpha" for i in items)

    def test_stats(self):
        ltm = LongTermMemoryStore(self.store, max_entries=100)
        s = ltm.stats()
        assert s["tier"] == "S3_long_term"


class TestWorldModel:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_entity_crud(self):
        wms = WorldModelStore(self.store)
        wms.upsert_entity("e1", "task", "Write tests", {"priority": "high"})
        e = wms.get_entity("e1")
        assert e["name"] == "Write tests"
        assert e["properties"]["priority"] == "high"

    def test_edge_crud(self):
        wms = WorldModelStore(self.store)
        wms.upsert_entity("a", "person", "Alice", {})
        wms.upsert_entity("b", "task", "Task B", {})
        wms.upsert_edge("a", "b", "assigned_to", weight=0.9)
        edges = wms.list_edges("a")
        assert len(edges) >= 1
        assert edges[0]["relation"] == "assigned_to"

    def test_stats(self):
        wms = WorldModelStore(self.store)
        s = wms.stats()
        assert s["tier"] == "S4_world_model"


# ── Tiered Memory Manager Integration ──────────────────────

class TestTieredMemoryManager:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_ingest_low_importance_only_s1(self):
        mgr = TieredMemoryManager(self.store)
        result = mgr.ingest("low importance", importance=0.3, source="test")
        assert "s1" in result
        assert "s2" not in result
        assert "s3" not in result

    def test_ingest_medium_importance_s1_s2(self):
        mgr = TieredMemoryManager(self.store)
        result = mgr.ingest("medium", importance=0.7, source="test")
        assert "s1" in result
        assert "s2" in result  # >= 0.6
        assert "s3" not in result  # < 0.8

    def test_ingest_high_importance_all_tiers(self):
        mgr = TieredMemoryManager(self.store)
        result = mgr.ingest("critical", importance=0.9, source="test")
        assert "s1" in result
        assert "s2" in result
        assert "s3" in result  # >= 0.8

    def test_recall_cross_tier(self):
        mgr = TieredMemoryManager(self.store)
        mgr.ingest("unique keyword zephyr", importance=0.9, source="test")
        results = mgr.recall("zephyr", tiers=[2, 3])
        assert len(results) >= 1

    def test_stats_all_tiers(self):
        mgr = TieredMemoryManager(self.store)
        s = mgr.stats()
        assert "S1_session" in s
        assert "S2_working" in s
        assert "S3_long_term" in s
        assert "S4_world_model" in s
        assert "S5_event_trace" in s

    def test_maintenance(self):
        mgr = TieredMemoryManager(self.store)
        result = mgr.maintenance()
        assert "s1_evicted" in result
        assert "s2_cleaned" in result
