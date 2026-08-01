"""Tests for new MVSC components: Verifier, Lifecycle, MVSC API routes."""

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from packages.contracts.protocols import Intent, ToolResult
from packages.agentos.verifier import Verifier
from packages.lifecycle.lifecycle import Heartbeat, LifecycleManager
from packages.contracts.state import RuntimeMode


# ═══════════════════════════════════════════════════════════
# Verifier tests
# ═══════════════════════════════════════════════════════════

class TestVerifier:
    @pytest.fixture
    def verifier(self):
        return Verifier()

    @pytest.mark.asyncio
    async def test_passing_result(self, verifier):
        intent = Intent(intent_id="i1", description="search for files")
        result = ToolResult(ok=True, data={"results": ["a.txt"]}, side_effects=["file:search"])
        vr = await verifier.verify(intent, None, result)
        assert vr.passed is True

    @pytest.mark.asyncio
    async def test_failing_result(self, verifier):
        intent = Intent(intent_id="i1", description="read file")
        result = ToolResult(ok=False, data={}, error="file not found")
        vr = await verifier.verify(intent, None, result)
        assert vr.passed is False
        assert any("Tool execution failed" in c["detail"] for c in vr.checks)

    @pytest.mark.asyncio
    async def test_dangerous_side_effects(self, verifier):
        intent = Intent(intent_id="i1", description="do something")
        result = ToolResult(ok=True, data={}, side_effects=["delete_critical"])
        vr = await verifier.verify(intent, None, result)
        assert vr.passed is False

    @pytest.mark.asyncio
    async def test_to_events_passed(self, verifier):
        intent = Intent(intent_id="i1", description="test")
        result = ToolResult(ok=True, data={})
        vr = await verifier.verify(intent, None, result)
        events = verifier.to_events(vr, "cause-1")
        assert len(events) == 1
        assert events[0].event_type == "verification.passed"

    @pytest.mark.asyncio
    async def test_to_events_failed(self, verifier):
        intent = Intent(intent_id="i1", description="test")
        result = ToolResult(ok=False, data={}, error="fail")
        vr = await verifier.verify(intent, None, result)
        events = verifier.to_events(vr, "cause-2")
        assert events[0].event_type == "verification.failed"

    @pytest.mark.asyncio
    async def test_stats_tracking(self, verifier):
        await verifier.verify(Intent(intent_id="i1", description="ok"), None,
                              ToolResult(ok=True, data={}))
        await verifier.verify(Intent(intent_id="i2", description="fail"), None,
                              ToolResult(ok=False, data={}, error="e"))
        s = verifier.stats
        assert s["total_checks"] == 2
        assert s["failed_checks"] == 1
        assert s["pass_rate"] == 0.5


# ═══════════════════════════════════════════════════════════
# Lifecycle tests
# ═══════════════════════════════════════════════════════════

class TestHeartbeat:
    def test_challenge_response_cycle(self):
        hb = Heartbeat()
        challenge = hb.issue_challenge()
        import hashlib
        response = hashlib.sha256((challenge + "eva-self-proof").encode()).hexdigest()[:16]
        assert hb.verify_response(challenge, response) is True
        assert hb.healthy is True

    def test_wrong_response_fails(self):
        hb = Heartbeat()
        challenge = hb.issue_challenge()
        assert hb.verify_response(challenge, "wrong") is False
        assert hb._missed_challenges == 1

    def test_three_misses_unhealthy(self):
        hb = Heartbeat()
        ch = hb.issue_challenge()
        for _ in range(3):
            hb.verify_response(ch, "wrong")
        assert hb.healthy is False


class TestLifecycleManager:
    def test_initial_mode_is_booting(self):
        lm = LifecycleManager()
        assert lm.mode == RuntimeMode.BOOTING

    def test_booting_to_active(self):
        lm = LifecycleManager()
        assert lm.transition(RuntimeMode.ACTIVE, "boot done") is True
        assert lm.mode == RuntimeMode.ACTIVE

    def test_active_to_degraded(self):
        lm = LifecycleManager()
        lm.transition(RuntimeMode.ACTIVE, "boot")
        lm.set_degraded("too many errors")
        assert lm.mode == RuntimeMode.DEGRADED

    def test_degraded_to_recovering(self):
        lm = LifecycleManager()
        lm.transition(RuntimeMode.ACTIVE, "boot")
        lm.set_degraded("errors")
        lm.set_recovering()
        assert lm.mode == RuntimeMode.RECOVERING

    def test_recovering_to_active(self):
        lm = LifecycleManager()
        lm.transition(RuntimeMode.ACTIVE, "boot")
        lm.set_degraded("errors")
        lm.set_recovering()
        lm.set_active()
        assert lm.mode == RuntimeMode.ACTIVE

    def test_invalid_transition_rejected(self):
        lm = LifecycleManager()
        # Can't go from BOOTING directly to REFLECTING
        assert lm.transition(RuntimeMode.REFLECTING, "nope") is False
        assert lm.mode == RuntimeMode.BOOTING

    def test_shutdown_from_any_state(self):
        lm = LifecycleManager()
        assert lm.transition(RuntimeMode.SHUTTING_DOWN, "system halt") is True
        assert lm.mode == RuntimeMode.SHUTTING_DOWN

    def test_history_recorded(self):
        lm = LifecycleManager()
        lm.transition(RuntimeMode.ACTIVE, "boot done")
        lm.transition(RuntimeMode.REFLECTING, "periodic")
        s = lm.stats
        assert len(s["history"]) == 2
