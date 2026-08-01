"""Tests for governance, observability, intent_parser, planner_dag."""

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.protocols import Intent
from packages.governance.governance import (
    AuditTrail,
    IdentityChangeManager,
    PermissionChecker,
)
from packages.observability.observability import (
    HealthReporter,
    MetricsCollector,
    Tracer,
)
from packages.agentos.intent_parser import IntentParser
from packages.agentos.planner_dag import PlannerDAG


# ═══════════════════════════════════════════════════════════
# Governance tests
# ═══════════════════════════════════════════════════════════

class TestPermissionChecker:
    def test_allows_normal_action(self):
        pc = PermissionChecker()
        allowed, reason = pc.check("read_file", token_valid=True)
        assert allowed is True

    def test_denies_expired_token(self):
        pc = PermissionChecker()
        allowed, reason = pc.check("read_file", token_valid=False)
        assert allowed is False

    def test_denies_high_risk_without_approval(self):
        pc = PermissionChecker()
        allowed, reason = pc.check("delete_memory", risk_level="low")
        assert allowed is False

    def test_denies_prohibited_action(self):
        pc = PermissionChecker()
        allowed, reason = pc.check("modify_constitution")
        assert allowed is False

    def test_stats_tracking(self):
        pc = PermissionChecker()
        pc.check("read", token_valid=True)
        pc.check("delete_memory", risk_level="low")
        assert pc.stats["allowed"] == 1
        assert pc.stats["denied"] == 1


class TestAuditTrail:
    def test_record_creates_entry(self):
        at = AuditTrail()
        h = at.record("read_file", "eva", "test.txt")
        assert len(at.recent()) == 1
        assert len(h) == 16  # hash length

    def test_chain_verification(self):
        at = AuditTrail()
        at.record("a", "eva")
        at.record("b", "eva")
        at.record("c", "eva")
        ok, msg = at.verify_chain()
        assert ok is True

    def test_chain_tamper_detection(self):
        at = AuditTrail()
        at.record("a", "eva")
        # Tamper
        at._entries[0]["action"] = "tampered"
        ok, msg = at.verify_chain()
        assert ok is False


class TestIdentityChangeManager:
    def test_propose_approve_flow(self):
        icm = IdentityChangeManager()
        pid = icm.propose("role_definition", "old", "new", "test reason")
        assert pid.startswith("icp_")
        assert len(icm.pending_proposals) == 1

        assert icm.approve(pid, "user") is True
        assert len(icm.pending_proposals) == 0
        assert icm.stats["approved"] == 1

    def test_reject_flow(self):
        icm = IdentityChangeManager()
        pid = icm.propose("role_definition", "old", "new", "reason")
        assert icm.reject(pid, "not needed") is True
        assert icm.stats["rejected"] == 1

    def test_risk_assessment(self):
        icm = IdentityChangeManager()
        pid = icm.propose("core_principles", "old", "new", "dangerous")
        proposals = icm.pending_proposals
        assert proposals[0]["risk_assessment"]["risk_level"] == "high"


# ═══════════════════════════════════════════════════════════
# Observability tests
# ═══════════════════════════════════════════════════════════

class TestMetricsCollector:
    def test_counters_and_gauges(self):
        mc = MetricsCollector()
        mc.increment("events", 5)
        mc.gauge("error_rate", 0.05)
        mc.observe("latency", 42.0)
        mc.observe("latency", 48.0)

        snap = mc.snapshot()
        assert snap["counters"]["events"] == 5
        assert snap["gauges"]["error_rate"] == 0.05
        assert "latency" in snap["histograms"]
        assert snap["histograms"]["latency"]["count"] == 2


class TestTracer:
    def test_span_lifecycle(self):
        t = Tracer()
        t.start_span("s1", operation="test")
        t.end_span("s1")

        span = t.get_trace("s1")
        assert span["status"] == "ok"
        assert span["duration_ms"] is not None

    def test_trace_tree(self):
        t = Tracer()
        t.start_span("root", operation="root")
        t.start_span("child", parent_id="root", operation="child")
        t.end_span("child")
        t.end_span("root")

        tree = t.get_trace_tree("root")
        assert len(tree) == 2


class TestHealthReporter:
    def test_overall_healthy(self):
        hr = HealthReporter()
        hr.report("db", True)
        hr.report("event_bus", True)
        assert hr.overall_healthy is True

    def test_degraded_when_check_fails(self):
        hr = HealthReporter()
        hr.report("db", True)
        hr.report("event_bus", False, "connection lost")
        assert hr.overall_healthy is False
        snap = hr.snapshot()
        assert snap["overall"] == "degraded"


# ═══════════════════════════════════════════════════════════
# IntentParser + PlannerDAG tests
# ═══════════════════════════════════════════════════════════

class TestIntentParser:
    @pytest.mark.asyncio
    async def test_user_message_intent(self):
        parser = IntentParser()
        event = EventEnvelope(
            event_type=EventFamily.PERCEPTION.USER_MESSAGE,
            source="user",
            payload={"text": "search for weather data urgently"},
        )
        intent = await parser.parse(event)
        assert intent.priority == 0.8
        assert "time_sensitive" in intent.constraints

    @pytest.mark.asyncio
    async def test_maintenance_intent(self):
        parser = IntentParser()
        event = EventEnvelope(
            event_type=EventFamily.LIFECYCLE.MAINTENANCE_STARTED,
            source="scheduler",
            payload={},
        )
        intent = await parser.parse(event)
        assert intent.priority == 0.5
        assert "maintenance" in intent.description.lower()


class TestPlannerDAG:
    @pytest.mark.asyncio
    async def test_search_intent_generates_two_steps(self):
        planner = PlannerDAG()
        intent = Intent(intent_id="i1", description="search for test files")
        plan = await planner.create(intent=intent)

        assert len(plan.steps) == 2
        assert plan.steps[0].agent_type == "search_agent"
        assert plan.steps[1].dependencies == [plan.steps[0].step_id]

    @pytest.mark.asyncio
    async def test_default_intent_single_step(self):
        planner = PlannerDAG()
        intent = Intent(intent_id="i1", description="hello")
        plan = await planner.create(intent=intent)

        assert len(plan.steps) == 1
        assert plan.steps[0].agent_type == "chat_agent"

    @pytest.mark.asyncio
    async def test_constraints_affect_steps(self):
        planner = PlannerDAG()
        intent = Intent(
            intent_id="i1",
            description="search carefully",
            constraints=["safety_critical"],
        )
        plan = await planner.create(intent=intent)
        for step in plan.steps:
            assert step.risk_level == "high"
