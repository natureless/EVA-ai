"""Unit tests for MemoryAPI — event, trace, episodic memory CRUD."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from event.event_schema import Event, TraceRecord
from memory.memory_api import MemoryAPI, EpisodicMemoryItem


class TestMemoryAPI:
    def test_append_event(self):
        store = MagicMock()
        store.dumps_json.side_effect = lambda obj: json.dumps(obj)
        api = MemoryAPI(store)
        event = Event(
            type="user_message", source="test",
            payload={"text": "hello"},
        )
        api.append_event(event)
        store.execute.assert_called_once()

    def test_append_event_raises_on_db_error(self):
        store = MagicMock()
        store.dumps_json.side_effect = lambda obj: json.dumps(obj)
        store.execute.side_effect = RuntimeError("db down")
        api = MemoryAPI(store)
        event = Event(type="user_message", source="test", payload={})
        with pytest.raises(RuntimeError):
            api.append_event(event)

    def test_write_trace(self):
        store = MagicMock()
        api = MemoryAPI(store)
        trace = TraceRecord(
            loop_id="loop-1",
            event_type="user_message",
            decision="act",
            agent="chat_agent",
            result_summary="ok",
            duration_ms=100,
        )
        api.write_trace(trace)
        store.execute.assert_called_once()

    def test_write_episodic_memory(self):
        store = MagicMock()
        store.dumps_json.side_effect = lambda obj: json.dumps(obj)
        api = MemoryAPI(store)
        mid = api.write_episodic_memory(
            event_type="user_message",
            summary="User said hello",
            payload={"text": "hello"},
            importance=0.8,
        )
        assert mid is not None
        assert len(mid) > 0
        store.execute.assert_called_once()

    def test_get_recent_memories(self):
        store = MagicMock()
        store.fetchall.return_value = [
            {"id": "m1", "timestamp": "2026-01-01T00:00:00Z",
             "event_type": "user_message", "summary": "hello",
             "payload": '{"text":"hello"}', "importance": 0.5},
        ]
        store.loads_json.side_effect = lambda s: json.loads(s)
        api = MemoryAPI(store)
        memories = api.get_recent_memories(limit=5)
        assert len(memories) == 1
        assert memories[0]["payload"] == {"text": "hello"}

    def test_get_recent_traces(self):
        store = MagicMock()
        store.fetchall.return_value = [
            {"id": "t1", "loop_id": "l1", "timestamp": "2026-01-01T00:00:00Z",
             "event_type": "user_message", "decision": "act",
             "agent": "chat_agent", "result_summary": "ok", "duration_ms": 50},
        ]
        api = MemoryAPI(store)
        traces = api.get_recent_traces(limit=5)
        assert len(traces) == 1
        assert traces[0]["agent"] == "chat_agent"

    def test_get_recent_events(self):
        store = MagicMock()
        store.fetchall.return_value = [
            {"id": "e1", "type": "user_message", "source": "test",
             "timestamp": "2026-01-01T00:00:00Z",
             "payload": '{"text":"hello"}', "correlation_id": "", "status": "new"},
        ]
        store.loads_json.side_effect = lambda s: json.loads(s)
        api = MemoryAPI(store)
        events = api.get_recent_events(limit=5)
        assert len(events) == 1
        assert events[0]["payload"] == {"text": "hello"}

    def test_get_recent_memories_empty(self):
        store = MagicMock()
        store.fetchall.return_value = []
        api = MemoryAPI(store)
        memories = api.get_recent_memories()
        assert memories == []


class TestEpisodicMemoryItem:
    def test_create_item(self):
        item = EpisodicMemoryItem(
            id="m1", timestamp="2026-01-01T00:00:00Z",
            event_type="user_message", summary="test",
            payload={}, importance=0.5,
        )
        assert item.id == "m1"
        assert item.importance == 0.5
