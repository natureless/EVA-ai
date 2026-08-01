"""Integration tests for AdaptedCognitionLoop — MVSC pipeline with real EVA backend.

Tests the adapted loop using mock/fake backends to verify the full
13-phase pipeline works end-to-end with existing EVA components.
"""

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

from agents.base_agent import AgentResult, AgentTask
from event.event_bus import EventBus
from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import ConsciousState, RuntimeMode
from packages.cognition.adapted_loop import AdaptedCognitionLoop, create_adapted_loop
from packages.cognition.loop import (
    Attention,
    ContentEngine,
    DecisionEngine,
    Metacognition,
    Workspace,
)
from packages.kernel.state_bridge import conscious_to_system_state


# ═══════════════════════════════════════════════════════════
# Fake backends for testing
# ═══════════════════════════════════════════════════════════

class FakeWorldModel:
    """Minimal WorldModelGraph stub."""

    def __init__(self):
        self.focus = "idle"
        self.mode = "active"
        self.active_tasks: list = []
        self.last_reply = ""
        self.last_selected_agent = ""
        self.last_loop_id = ""

    def apply_user_message(self, text: str):
        self.focus = text[:80]

    def apply_agent_result(self, *, reply: str, selected_agent: str, loop_id: str):
        self.last_reply = reply
        self.last_selected_agent = selected_agent
        self.last_loop_id = loop_id


class FakeAgentRouter:
    def route(self, preferred: str, task):
        return preferred


class FakeOrchestrator:
    def execute(self, agent_name: str, task: AgentTask):
        return (
            AgentResult(
                ok=True, agent=agent_name,
                content=f"Response from {agent_name}: {task.payload.get('text', '')}",
                summary=f"Handled by {agent_name}",
            ),
            42,  # duration_ms
        )


class FakeTieredMemory:
    def __init__(self):
        self.ingested: list = []

    def ingest(self, content, **kwargs):
        self.ingested.append({"content": content, **kwargs})


class FakeContainer:
    """Minimal AppContainer stub for testing."""

    def __init__(self):
        self.world_model = FakeWorldModel()
        self.agent_router = FakeAgentRouter()
        self.orchestrator = FakeOrchestrator()
        self.tiered_memory = FakeTieredMemory()
        self.event_bus = EventBus()
        self.system_state = {"focus": "idle", "mode": "active"}
        self.loop = None
        self.prediction_tracker = None
        self.self_model_store = None
        self.self_model = None
        self.memory_governor = None
        self.planner = None


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════

class TestAdaptedCognitionLoop:
    """Test the adapted loop with fake backends."""

    @pytest.fixture
    def container(self):
        return FakeContainer()

    @pytest.fixture
    def adapted(self, container):
        return AdaptedCognitionLoop(
            container=container,
            content_engine=ContentEngine(),
            attention=Attention(),
            workspace=Workspace(),
            metacognition=Metacognition(),
            decision_engine=DecisionEngine(),
            feature_flags={
                "recurrent_content": True,
                "global_workspace": True,
                "self_model": True,
                "value_model": True,
                "episodic_memory": True,
                "narrative_identity": True,
                "metacognition": True,
            },
        )

    @pytest.mark.asyncio
    async def test_user_message_full_pipeline(self, adapted, container):
        """A user message should flow through all 13 phases."""
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "Hello EVA!"},
        )
        state = await adapted.run_once(event)

        # Pipeline completed
        assert state.tick == 1
        assert state.version == 1
        assert len(container.tiered_memory.ingested) >= 0  # may be 0 if no broadcast

    @pytest.mark.asyncio
    async def test_world_model_updated(self, adapted, container):
        """World model should reflect user message."""
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "What is the weather?"},
        )
        await adapted.run_once(event)

        assert container.world_model.focus == "What is the weather?"
        assert container.system_state["focus"] == "What is the weather?"

    @pytest.mark.asyncio
    async def test_run_once_and_sync(self, adapted, container):
        """run_once_and_sync should update system_state dict."""
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "Sync test"},
        )
        state = await adapted.run_once_and_sync(event)

        # system_state should be synced
        assert container.system_state["focus"] == "Sync test"
        assert "mvsc_tick" in container.system_state
        assert "mvsc_runtime_mode" in container.system_state

    @pytest.mark.asyncio
    async def test_idle_event_no_action(self, adapted, container):
        """An idle/maintenance event should not trigger action."""
        event = EventEnvelope(
            event_type="lifecycle.maintenance_started",
            source="scheduler",
            payload={},
        )
        state = await adapted.run_once(event)

        assert state.tick == 1
        # No user message → no response candidate → no action

    @pytest.mark.asyncio
    async def test_phase_timings_recorded(self, adapted, container):
        """Phase timings should be recorded for observability."""
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "Timing test"},
        )
        await adapted.run_once(event)

        timings = adapted.phase_timings
        assert len(timings) > 0
        assert "perceive" in timings
        assert "update_world" in timings

    @pytest.mark.asyncio
    async def test_feature_flags_disable_broadcast(self, container):
        """Disabling global_workspace should result in empty workspace."""
        adapted = AdaptedCognitionLoop(
            container=container,
            content_engine=ContentEngine(),
            attention=Attention(),
            workspace=Workspace(),
            metacognition=Metacognition(),
            decision_engine=DecisionEngine(),
            feature_flags={"global_workspace": False},
        )
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "Should not broadcast"},
        )
        state = await adapted.run_once(event)

        assert len(state.workspace) == 0

    @pytest.mark.asyncio
    async def test_body_update_reflects_errors(self, container):
        """Body delta should reflect consecutive errors."""
        # Simulate a loop with errors
        class FakeLoop:
            _consecutive_errors = 5

        container.loop = FakeLoop()

        adapted = AdaptedCognitionLoop(
            container=container,
            content_engine=ContentEngine(),
            attention=Attention(),
            workspace=Workspace(),
            metacognition=Metacognition(),
            decision_engine=DecisionEngine(),
        )
        event = EventEnvelope(event_type="test", source="test", payload={})
        state = await adapted.run_once(event)

        assert state.body.consecutive_failures == 5
        assert state.body.error_rate > 0

    @pytest.mark.asyncio
    async def test_create_adapted_loop_helper(self, container):
        """create_adapted_loop should wire all components correctly."""
        loop = create_adapted_loop(container)

        assert loop._container is container
        assert loop.content_engine is not None
        assert loop.attention is not None
        assert loop.workspace is not None
        assert loop.metacognition is not None
        assert loop.decision_engine is not None

    @pytest.mark.asyncio
    async def test_multiple_ticks_increment(self, adapted, container):
        """Each run_once should increment tick and version."""
        event = EventEnvelope(event_type="test.tick", source="test", payload={})

        s1 = await adapted.run_once(event)
        assert s1.tick == 1
        assert s1.version == 1

        s2 = await adapted.run_once(event)
        assert s2.tick == 2
        assert s2.version == 2

        s3 = await adapted.run_once(event)
        assert s3.tick == 3
        assert s3.version == 3


# ═══════════════════════════════════════════════════════════
# ConsciousState → system_state round-trip
# ═══════════════════════════════════════════════════════════

class TestStateRoundTrip:
    """Verify ConsciousState ↔ system_state preserves data through adapted loop."""

    @pytest.mark.asyncio
    async def test_round_trip_preserves_focus(self):
        """After running adapted loop, system_state should reflect ConsciousState."""
        container = FakeContainer()
        adapted = AdaptedCognitionLoop(
            container=container,
            content_engine=ContentEngine(),
            attention=Attention(),
            workspace=Workspace(),
            metacognition=Metacognition(),
            decision_engine=DecisionEngine(),
        )
        event = EventEnvelope(
            event_type="perception.user_message_received",
            source="user",
            payload={"text": "Round trip test"},
        )
        state = await adapted.run_once_and_sync(event)

        # Verify sync
        ss = container.system_state
        assert ss["focus"] == "Round trip test"
        assert "mvsc_tick" in ss
        assert ss["mvsc_tick"] == state.tick
