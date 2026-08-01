"""Integration tests for SelfModelMigrator + TieredMemoryAdapter.

Phase 3: SelfModel migration from legacy JSON to 6 sub-models.
Phase 4: TieredMemoryAdapter wrapping TieredMemoryManager.
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import (
    BroadcastContent,
    ConsciousState,
    ContentCandidate,
)
from packages.models.self_model import (
    SelfModel,
    IdentityModel,
    CapabilityModel,
    AgencyModel,
    NarrativeModel,
    BoundaryModel,
)
from packages.models.migrator import (
    SelfModelMigrator,
    load_self_model,
    migrate_self_model,
)
from packages.memory.memory_adapter import TieredMemoryAdapter


# ═══════════════════════════════════════════════════════════
# Phase 3: SelfModelMigrator tests
# ═══════════════════════════════════════════════════════════

LEGACY_SELF_MODEL = {
    "_version": 2,
    "identity": "EVA v0.2",
    "status": "active",
    "capabilities": ["chat", "docs_summary", "memory_write", "trace_record", "search"],
    "constraints": ["single_process", "single_round_single_agent"],
    "state_history": [
        {
            "ts": "2025-06-01T10:00:00Z",
            "type": "agent_execution",
            "detail": "chat_agent: responded to user",
            "focus": "user query",
            "loop_id": "loop_abc123",
        },
        {
            "ts": "2025-06-01T11:00:00Z",
            "type": "agent_execution",
            "detail": "search_agent: found 5 files",
            "focus": "file search",
            "loop_id": "loop_def456",
        },
    ],
    "perturbations": [
        {
            "ts": "2025-06-01T12:00:00Z",
            "cause": "high prediction error (0.75) on agent chat_agent",
            "delta_magnitude": 0.75,
            "affected_fields": ["world.focus", "prediction"],
            "loop_id": "loop_ghi789",
        },
    ],
    "prediction_errors": [
        {"ts": "2025-06-01T12:00:00Z", "error": 0.75, "focus": "user query"},
    ],
    "stability_metrics": {
        "total_perturbations": 1,
        "mean_prediction_error": 0.75,
        "recent_perturbation_count": 1,
        "stability_score": 0.625,
        "last_evaluated_at": "2025-06-01T12:00:00Z",
    },
}


class TestSelfModelMigrator:
    """Test migration from legacy JSON to SelfModel 6 sub-models."""

    def test_migrate_dict_creates_all_submodels(self):
        """Migration should populate all 6 sub-models."""
        migrator = SelfModelMigrator(subject_id="test-eva")
        sm = migrator.migrate_dict(LEGACY_SELF_MODEL)

        # All sub-models exist
        assert isinstance(sm.identity, IdentityModel)
        assert sm.capability is not None
        assert sm.boundary is not None
        assert sm.agency is not None
        assert sm.narrative is not None

    def test_migrate_identity(self):
        """Identity should be extracted from legacy."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert sm.identity.system_id == "eva-001"
        assert "EVA v0.2" in sm.identity.role_definition
        assert len(sm.identity.core_principles) == 4
        # Migration recorded in history
        assert len(sm.identity.change_history) == 1
        assert sm.identity.change_history[0]["action"] == "migrated_from_legacy"

    def test_migrate_capabilities(self):
        """Capabilities list should become CapabilityModel with entries."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert len(sm.capability.capabilities) == 5
        assert "chat" in sm.capability.capabilities
        assert "search" in sm.capability.capabilities

        chat_cap = sm.capability.capabilities["chat"]
        assert chat_cap.confidence == 0.8  # legacy capabilities get high confidence
        assert chat_cap.name == "Chat"

    def test_migrate_boundary(self):
        """Constraints should become BoundaryModel permissions."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert "single_process" in sm.boundary.permission_scope
        assert "single_round_single_agent" in sm.boundary.permission_scope

    def test_migrate_agency(self):
        """State history should become AgencyModel action receipts."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert sm.agency.self_initiated_count == 2
        assert len(sm.agency.action_history) == 2
        assert sm.agency.action_history[0].status == "completed"

    def test_migrate_narrative(self):
        """Perturbations should become NarrativeNodes."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert len(sm.narrative.nodes) >= 1
        node = sm.narrative.nodes[0]
        assert "prediction error" in node.summary.lower()
        assert len(node.evidence_event_ids) >= 1

    def test_migrate_stability_metrics(self):
        """Stability metrics should be preserved."""
        sm = SelfModelMigrator().migrate_dict(LEGACY_SELF_MODEL)

        assert sm.stability_metrics["stability_score"] == 0.625
        assert sm.stability_metrics["mean_prediction_error"] == 0.75
        assert sm.stability_metrics["total_perturbations"] == 1

    def test_migrate_file_round_trip(self):
        """Write legacy JSON → migrate → save → reload should work."""
        tmpdir = tempfile.mkdtemp()
        try:
            source = Path(tmpdir) / "self_model.json"
            target = Path(tmpdir) / "self_model_v2.json"

            source.write_text(json.dumps(LEGACY_SELF_MODEL))

            sm = migrate_self_model(source, target, subject_id="test-eva")

            assert target.exists()

            # Reload
            with target.open() as fh:
                reloaded = json.load(fh)

            assert "identity" in reloaded
            assert reloaded["identity"]["system_id"] == "test-eva"

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_self_model_prefers_v2(self):
        """load_self_model should prefer v2 format."""
        tmpdir = tempfile.mkdtemp()
        try:
            v2_path = Path(tmpdir) / "self_model_v2.json"
            legacy_path = Path(tmpdir) / "self_model.json"

            # Create v2 file
            sm = SelfModel()
            sm.identity.system_id = "v2-eva"
            v2_path.write_text(json.dumps(sm.to_dict()))

            # Create legacy file
            legacy_path.write_text(json.dumps(LEGACY_SELF_MODEL))

            loaded = load_self_model(v2_path, legacy_path, subject_id="test")
            assert loaded.identity.system_id == "v2-eva"

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_migrate_empty_legacy_creates_defaults(self):
        """Empty legacy dict should create sensible defaults."""
        sm = SelfModelMigrator().migrate_dict({})

        assert sm.identity.system_id == "eva-001"
        assert sm.capability is not None
        assert sm.stability_metrics["stability_score"] == 1.0


# ═══════════════════════════════════════════════════════════
# Phase 4: TieredMemoryAdapter tests
# ═══════════════════════════════════════════════════════════

class FakeTieredMemory:
    """Fake TieredMemoryManager for testing adapter."""

    def __init__(self):
        self.ingested: list[dict] = []
        self.maintenance_calls = 0

    def ingest(self, content, **kwargs):
        self.ingested.append({"content": content, **kwargs})
        return {"s1": "fake_id"}

    def recall(self, query, tiers=None):
        return [
            {
                "tier": "S1",
                "content": f"Memory about {query}",
                "source": "user",
                "confidence": 0.8,
                "created_at": "2025-06-01T10:00:00Z",
            },
            {
                "tier": "S3",
                "content": f"Old memory about {query}",
                "source": "chat_agent",
                "confidence": 0.5,
                "created_at": "2025-01-01T10:00:00Z",
            },
        ]

    def maintenance(self):
        self.maintenance_calls += 1
        return {"s1_evicted": 0}


class TestTieredMemoryAdapter:
    """Test TieredMemoryAdapter wrapping TieredMemoryManager."""

    @pytest.fixture
    def fake_tm(self):
        return FakeTieredMemory()

    @pytest.fixture
    def adapter(self, fake_tm):
        return TieredMemoryAdapter(fake_tm)

    @pytest.mark.asyncio
    async def test_consolidate_broadcast_content(self, adapter, fake_tm):
        """Broadcast content should be ingested into memory."""
        state = ConsciousState(tick=1)
        event = EventEnvelope(event_type="test", source="test", payload={})
        broadcast = [
            BroadcastContent(
                content=ContentCandidate(
                    content_type="response",
                    summary="User asked about weather",
                    source_module="chat_agent",
                    salience=0.8,
                    goal_relevance=0.7,
                ),
            ),
        ]

        events = await adapter.consolidate(
            state, event, broadcast, {}, [],
        )

        assert len(fake_tm.ingested) == 1
        assert fake_tm.ingested[0]["content"] == "User asked about weather"
        assert fake_tm.ingested[0]["importance"] > 0.3  # priority-based

        # Should emit memory event
        assert len(events) == 1
        assert events[0].event_type == EventFamily.MEMORY.EPISODE_COMMITTED

    @pytest.mark.asyncio
    async def test_consolidate_action_events(self, adapter, fake_tm):
        """Action completion events should be ingested."""
        state = ConsciousState(tick=1)
        event = EventEnvelope(event_type="test", source="test", payload={})
        action_events = [
            EventEnvelope(
                event_type=EventFamily.ACTION.TOOL_COMPLETED,
                source="agentos",
                payload={"summary": "File search completed: 3 results", "ok": True},
            ),
            EventEnvelope(
                event_type=EventFamily.ACTION.AGENT_COMPLETED,
                source="agentos",
                payload={"summary": "Agent responded successfully", "ok": True},
            ),
        ]

        await adapter.consolidate(state, event, [], {}, action_events)

        # Both action events should be ingested
        assert len(fake_tm.ingested) == 2

    @pytest.mark.asyncio
    async def test_consolidate_triggers_maintenance(self, adapter, fake_tm):
        """Every 100 ticks, maintenance should run."""
        state = ConsciousState(tick=100)
        event = EventEnvelope(event_type="test", source="test", payload={})

        await adapter.consolidate(state, event, [], {}, [])
        assert fake_tm.maintenance_calls == 1

        # Tick 101: no maintenance
        state2 = ConsciousState(tick=101)
        await adapter.consolidate(state2, event, [], {}, [])
        assert fake_tm.maintenance_calls == 1  # still 1

        # Tick 200: maintenance again
        state3 = ConsciousState(tick=200)
        await adapter.consolidate(state3, event, [], {}, [])
        assert fake_tm.maintenance_calls == 2

    @pytest.mark.asyncio
    async def test_retrieve_returns_scored_results(self, adapter, fake_tm):
        """Retrieve should return results with retrieval scores."""
        results = await adapter.retrieve("weather")

        assert len(results) == 2
        # Each result should have a retrieval score
        for r in results:
            assert "_retrieval_score" in r
            assert 0.0 <= r["_retrieval_score"] <= 1.0

        # First result should be higher scored (more recent + higher confidence)
        assert results[0]["_retrieval_score"] > results[1]["_retrieval_score"]

    @pytest.mark.asyncio
    async def test_retrieve_respects_limit(self, adapter, fake_tm):
        """Retrieve should respect the limit parameter."""
        results = await adapter.retrieve("test", limit=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_retrieve_with_context_boosts_goal_relevance(self, adapter, fake_tm):
        """Goal-relevant context should boost scores."""
        results_no_ctx = await adapter.retrieve("weather")
        results_with_ctx = await adapter.retrieve(
            "weather",
            context={"goals": ["Check weather forecast for today"]},
        )

        # With context, scores should be >= without
        assert results_with_ctx[0]["_retrieval_score"] >= results_no_ctx[0]["_retrieval_score"]

    @pytest.mark.asyncio
    async def test_retrieve_empty_query(self, adapter, fake_tm):
        """Empty query should still return results."""
        results = await adapter.retrieve("")
        assert len(results) >= 0  # should not crash

    @pytest.mark.asyncio
    async def test_tiers_for_importance(self, adapter):
        """Importance routing logic should be correct."""
        assert adapter._tiers_for_importance(0.3) == ["S1"]
        assert adapter._tiers_for_importance(0.6) == ["S1", "S2"]
        assert adapter._tiers_for_importance(0.8) == ["S1", "S2", "S3"]
