"""Tests for S1 session memory persistence, event bus durability,
and policy state machine trigger completeness."""

import json
import tempfile
import os
from pathlib import Path

from memory.sqlite_store import SQLiteStore
from memory.tiered_store import SessionMemory, WorkingMemoryStore, TieredMemoryManager
from event.event_bus import EventBus
from event.event_schema import Event


class TestS1Persistence:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_dump_to_s2(self):
        s1 = SessionMemory(max_entries=10, ttl_seconds=3600)
        s2 = WorkingMemoryStore(self.store, max_entries=100)

        s1.put("k1", {"content": "hello world", "source": "test", "importance": 0.7})
        s1.put("k2", {"content": "goodbye world", "source": "test", "importance": 0.5})

        count = s1.dump_to_s2(s2)
        assert count == 2

        recent = s2.list_recent(limit=50)
        contents = [r["content"] for r in recent]
        assert any("hello" in c for c in contents)
        assert any("goodbye" in c for c in contents)

    def test_restore_from_s2(self):
        s1 = SessionMemory(max_entries=10, ttl_seconds=3600)
        s2 = WorkingMemoryStore(self.store, max_entries=100)

        s2.put("restored content A", summary="A", source="s1_dump", tags=["s1_dump"])
        s2.put("restored content B", summary="B", source="s1_dump", tags=["s1_dump"])

        count = s1.restore_from_s2(s2)
        assert count >= 2

        stats = s1.stats()
        assert stats["entries"] >= 2


class TestEventBusPersistence:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_persist_event_to_s5(self):
        from memory.tiered_store import EventTraceStore
        s5 = EventTraceStore(self.store)
        bus = EventBus(s5_store=s5)

        event = Event(
            type="user_message",
            source="test",
            payload={"text": "hello"},
            correlation_id="corr-001",
        )
        bus.publish(event)
        assert bus.size() == 1
        stats = bus.stats()
        assert stats["queue_size"] == 1

        # consume clears the queue
        consumed = bus.consume(timeout=0.1)
        assert consumed is not None
        assert consumed.type == "user_message"
        bus.task_done()

    def test_stats(self):
        bus = EventBus()
        assert bus.size() == 0
        stats = bus.stats()
        assert "queue_size" in stats
        assert "published" in stats
        assert "persist_failures" in stats


class TestPolicyTriggers:
    def test_missing_triggers_now_allowed(self):
        """Verify that scheduled_task and command_failed are valid transitions."""
        from core.policy_engine import StateMachine, State
        sm = StateMachine()

        # scheduled_task: DORMANT → COMMANDED
        decision = sm.transition("scheduled_task")
        assert decision.verdict == "allow"
        assert sm.current == State.COMMANDED

        sm2 = StateMachine()
        sm2.transition("user_command")
        # command_failed: COMMANDED → DORMANT
        decision2 = sm2.transition("command_failed")
        assert decision2.verdict == "allow"
        assert sm2.current == State.DORMANT

    def test_timeout_transition(self):
        from core.policy_engine import StateMachine, State
        sm = StateMachine()
        sm.transition("user_command")
        decision = sm.transition("timeout")
        assert decision.verdict == "allow"
        assert sm.current == State.DORMANT
