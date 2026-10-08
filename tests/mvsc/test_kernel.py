"""Integration tests for EventBusAdapter + StateBridge.

Validates that the new MVSC kernel layer correctly wraps existing
EVA components without breaking backward compatibility.
"""

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

from event.event_bus import EventBus
from event.event_schema import Event as LegacyEvent
from packages.contracts.events import EventEnvelope
from packages.contracts.state import RuntimeMode
from packages.kernel.event_bus_adapter import EventBusAdapter, to_legacy_event
from packages.kernel.event_store import EventStore
from packages.kernel.state_bridge import (
    conscious_to_system_state,
    sync_system_state,
    system_state_to_conscious,
)


# ═══════════════════════════════════════════════════════════
# EventBusAdapter tests
# ═══════════════════════════════════════════════════════════

class TestEventBusAdapter:
    """Test EventBusAdapter wrapping existing EventBus."""

    @pytest.fixture
    def legacy_bus(self):
        return EventBus()

    @pytest.fixture
    def event_store(self):
        db = os.path.join(tempfile.gettempdir(), "test_adapter_store.db")
        if os.path.exists(db):
            os.unlink(db)
        store = EventStore(db)
        yield store
        store.close()
        try:
            os.unlink(db)
        except Exception:
            pass

    @pytest.fixture
    def adapter(self, legacy_bus):
        return EventBusAdapter(legacy_bus)

    @pytest.fixture
    def adapter_with_store(self, legacy_bus, event_store):
        return EventBusAdapter(legacy_bus, event_store)

    # ── publish ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_publish_envelope(self, adapter, legacy_bus):
        """Publishing an EventEnvelope should enqueue to legacy bus."""
        evt = EventEnvelope(
            event_type="perception.user_message_received",
            source="test",
            payload={"text": "hello"},
        )
        result = await adapter.publish(evt)
        assert result is True
        assert adapter._published_count == 1
        assert legacy_bus.size() == 1

    @pytest.mark.asyncio
    async def test_publish_legacy(self, adapter, legacy_bus):
        """Publishing a LegacyEvent should auto-upgrade and enqueue."""
        legacy = LegacyEvent(type="user_message", source="test", payload={"text": "hi"})
        result = await adapter.publish_legacy(legacy)
        assert result is True
        assert legacy_bus.size() == 1

    @pytest.mark.asyncio
    async def test_publish_persists_to_store(self, adapter_with_store, legacy_bus, event_store):
        """Events should be persisted to EventStore when configured."""
        evt = EventEnvelope(
            event_type="perception.user_message_received",
            source="test",
            payload={"text": "persist me"},
        )
        await adapter_with_store.publish(evt)
        assert adapter_with_store._persisted_count == 1
        assert event_store.get_latest_sequence() == 1

    # ── consume ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_consume_returns_envelope(self, adapter, legacy_bus):
        """Consuming should return EventEnvelope (not LegacyEvent)."""
        legacy = LegacyEvent(type="user_message", source="test", payload={"text": "x"})
        legacy_bus.publish(legacy)

        result = await adapter.consume(timeout=0.1)
        assert result is not None
        assert isinstance(result, EventEnvelope)
        assert result.event_type == "perception.user_message_received"
        assert result.event_id == legacy.id
        assert result.payload == {"text": "x"}

    @pytest.mark.asyncio
    async def test_consume_empty_queue(self, adapter):
        """Consuming from empty queue should return None."""
        result = await adapter.consume(timeout=0.05)
        assert result is None

    # ── subscribe ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_subscribe_receives_events(self, adapter, legacy_bus):
        """Subscribers should receive matching events."""
        received: list[EventEnvelope] = []

        async def handler(event: EventEnvelope):
            received.append(event)

        adapter.subscribe("perception.", handler)

        evt = EventEnvelope(
            event_type="perception.user_message_received",
            source="test",
            payload={"text": "sub"},
        )
        await adapter.publish(evt)

        # Give async handlers time to run
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].event_type == "perception.user_message_received"

    @pytest.mark.asyncio
    async def test_subscribe_prefix_filtering(self, adapter, legacy_bus):
        """Only handlers matching the event_type prefix should fire."""
        perception_events: list[EventEnvelope] = []
        action_events: list[EventEnvelope] = []

        async def on_perception(event):
            perception_events.append(event)

        async def on_action(event):
            action_events.append(event)

        adapter.subscribe("perception.", on_perception)
        adapter.subscribe("action.", on_action)

        await adapter.publish(EventEnvelope(
            event_type="perception.user_message_received", source="test", payload={},
        ))
        await asyncio.sleep(0.1)

        assert len(perception_events) == 1
        assert len(action_events) == 0

    # ── round-trip ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_round_trip_preserves_data(self, adapter, legacy_bus):
        """Publish → consume should preserve core event data.

        Versioned metadata survives the stable facade and EventStore.
        """
        original = EventEnvelope(
            event_type="perception.user_message_received",
            source="test_user",
            payload={"text": "hello world", "nested": {"key": "value"}},
            confidence=0.9,
            priority=0.7,
        )
        await adapter.publish(original)
        consumed = await adapter.consume(timeout=0.1)

        assert consumed is not None
        assert consumed.event_id == original.event_id
        assert consumed.event_type == original.event_type
        assert consumed.source == original.source
        assert consumed.payload == original.payload
        assert consumed.confidence == original.confidence
        assert consumed.priority == original.priority
        assert consumed.correlation_id == original.correlation_id

    # ── stats ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stats_tracks_counts(self, adapter, legacy_bus):
        """Stats should reflect publish/consume activity."""
        await adapter.publish(EventEnvelope(
            event_type="perception.scheduler_tick", source="test", payload={},
        ))
        legacy = LegacyEvent(type="user_message", source="test", payload={})
        legacy_bus.publish(legacy)
        await adapter.consume(timeout=0.1)

        stats = adapter.stats()
        assert stats["adapter_published"] >= 1
        assert stats["adapter_consumed"] >= 1


# ═══════════════════════════════════════════════════════════
# to_legacy_event conversion tests
# ═══════════════════════════════════════════════════════════

class TestLegacyConversion:
    """Test EventEnvelope → LegacyEvent conversion."""

    def test_user_message_conversion(self):
        env = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "hello"},
        )
        legacy = to_legacy_event(env)
        assert legacy.type == "user_message"
        assert legacy.source == "user"
        assert legacy.payload == {"text": "hello"}
        assert legacy.id == env.event_id

    def test_maintenance_conversion(self):
        env = EventEnvelope(
            event_type="lifecycle.maintenance_started",
            source="scheduler",
            payload={},
        )
        legacy = to_legacy_event(env)
        assert legacy.type == "maintenance"

    def test_unknown_type_is_rejected(self):
        env = EventEnvelope(
            event_type="custom.unknown_event",
            source="test",
            payload={},
        )
        with pytest.raises(ValueError, match="no stable consumer"):
            to_legacy_event(env)


# ═══════════════════════════════════════════════════════════
# StateBridge tests
# ═══════════════════════════════════════════════════════════

class TestStateBridge:
    """Test system_state ↔ ConsciousState conversion."""

    @pytest.fixture
    def sample_system_state(self):
        """Representative system_state dict from bootstrap."""
        return {
            "ready": True,
            "db_ready": True,
            "event_bus_ready": True,
            "planner_ready": True,
            "loop_ready": True,
            "registry_ready": True,
            "result_registry_ready": True,
            "snapshot_ready": True,
            "profile_ready": True,
            "persona_ready": True,
            "self_model_ready": True,
            "scheduler_ready": True,
            "proactive_ready": True,
            "scheduler_running": True,
            "focus": "answering user query",
            "mode": "active",
            "active_tasks": [{"name": "respond to user", "status": "active"}],
            "pending_events": 0,
            "pending_results": 0,
            "last_reply": "Hello!",
            "last_selected_agent": "chat_agent",
            "last_loop_id": "loop_abc123",
            "last_loop_at": "2025-01-01T00:00:00Z",
            "last_snapshot_at": None,
            "last_proactive_reason": None,
            "last_context_summary": {"summary": "Active: respond to user"},
            "agents": ["chat_agent", "search_agent", "coding_agent", "docs_agent"],
            "stability_score": 0.95,
            "mean_prediction_error": 0.05,
            "total_perturbations": 3,
            "policy_state": {
                "state_machine": {"current": "dormant"},
                "tokens": {"active_tokens": 0},
            },
            "storage_backend": "sqlite",
            "bootstrap_sec": 1.5,
            "diagnostic": {"overall": "healthy", "score": 100},
        }

    def test_system_state_to_conscious(self, sample_system_state):
        """Conversion should preserve all relevant fields."""
        cs = system_state_to_conscious(sample_system_state)

        assert cs.subject_id == "eva-001"
        assert cs.runtime_mode == RuntimeMode.ACTIVE
        assert cs.world["focus"] == "answering user query"
        assert cs.world["active_tasks"] == sample_system_state["active_tasks"]
        assert cs.health["ready"] is True
        assert cs.health["pending_events"] == 0
        assert cs.relations["agents"] == sample_system_state["agents"]

        metrics = cs.self_model.get("stability_metrics", {})
        assert metrics["stability_score"] == 0.95
        assert metrics["mean_prediction_error"] == 0.05

    def test_conscious_to_system_state(self, sample_system_state):
        """Round-trip should not lose data."""
        cs = system_state_to_conscious(sample_system_state)
        ss = conscious_to_system_state(cs)

        # Core fields preserved
        assert ss["focus"] == "answering user query"
        assert ss["mode"] == "active"
        assert ss["active_tasks"] == sample_system_state["active_tasks"]
        assert ss["last_reply"] == "Hello!"
        assert ss["last_selected_agent"] == "chat_agent"
        assert ss["agents"] == sample_system_state["agents"]

        # Health preserved
        assert ss["ready"] is True
        assert ss["pending_events"] == 0

        # Self-model metrics preserved
        assert ss["stability_score"] == 0.95
        assert ss["mean_prediction_error"] == 0.05

        # New MVSC fields added
        assert ss["mvsc_runtime_mode"] == "active"
        assert ss["mvsc_tick"] == 0
        assert ss["mvsc_version"] == 0

    def test_sync_system_state_updates_in_place(self, sample_system_state):
        """sync_system_state should mutate the dict in place."""
        cs = system_state_to_conscious(sample_system_state)
        cs = cs.apply(world_delta={"focus": "new focus"})

        original_id = id(sample_system_state)
        sync_system_state(sample_system_state, cs)

        # Same dict object
        assert id(sample_system_state) == original_id
        # Updated values
        assert sample_system_state["focus"] == "new focus"
        # MVSC fields added
        assert "mvsc_runtime_mode" in sample_system_state

    def test_booting_mode(self):
        """System that isn't ready should be in BOOTING mode."""
        ss = {"ready": False}
        cs = system_state_to_conscious(ss)
        assert cs.runtime_mode == RuntimeMode.BOOTING

    def test_quarantined_mode(self):
        """Quarantined system should be in DEGRADED mode."""
        ss = {
            "ready": True,
            "policy_state": {
                "state_machine": {"current": "quarantined"},
            },
        }
        cs = system_state_to_conscious(ss)
        assert cs.runtime_mode == RuntimeMode.DEGRADED

    def test_raw_fields_preserved(self):
        """Unknown fields should be preserved in health._raw."""
        ss = {
            "focus": "test",
            "custom_field_xyz": "preserve me",
            "another_custom": 42,
        }
        cs = system_state_to_conscious(ss)
        raw = cs.health.get("_raw", {})
        assert raw["custom_field_xyz"] == "preserve me"
        assert raw["another_custom"] == 42

        # Round-trip
        ss2 = conscious_to_system_state(cs)
        assert ss2["custom_field_xyz"] == "preserve me"
        assert ss2["another_custom"] == 42
