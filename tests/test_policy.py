"""Policy engine unit + integration tests."""

from core.policy_engine import (
    PolicyEngine,
    StateMachine,
    State,
    Priority,
    PriorityResolver,
    TokenManager,
    Verdict,
)


# ── State Machine ──────────────────────────────────────────

class TestStateMachine:
    def test_initial_state_is_dormant(self):
        sm = StateMachine()
        assert sm.current == State.DORMANT

    def test_user_command_transitions_to_commanded(self):
        sm = StateMachine()
        d = sm.transition("user_command")
        assert d.verdict == Verdict.ALLOW
        assert sm.current == State.COMMANDED

    def test_command_completed_returns_to_dormant(self):
        sm = StateMachine()
        sm.transition("user_command")
        sm.transition("command_completed")
        assert sm.current == State.DORMANT

    def test_invalid_transition_denied(self):
        sm = StateMachine()
        d = sm.transition("command_completed")  # DORMANT → commanded_completed is invalid
        assert d.verdict == Verdict.DENY
        assert sm.current == State.DORMANT

    def test_quarantine_blocks_execution(self):
        sm = StateMachine()
        sm.transition("violation_detected")
        assert sm.current == State.QUARANTINED
        d = sm.can_execute(Priority.P0)
        assert d.verdict == Verdict.DENY
        assert "quarantined" in d.reason.lower()

    def test_quarantine_recovery(self):
        sm = StateMachine()
        sm.transition("violation_detected")
        assert sm.current == State.QUARANTINED
        sm.transition("manual_intervention")
        assert sm.current == State.DORMANT

    def test_state_history_tracked(self):
        sm = StateMachine()
        sm.transition("user_command")
        sm.transition("command_completed")
        state = sm.get_state()
        assert state["history_size"] == 2
        assert len(state["history"]) == 2

    def test_timeout_detection(self):
        sm = StateMachine()
        sm._timeout_minutes[State.COMMANDED] = -1  # force immediate timeout
        sm.transition("user_command")
        assert sm.is_timed_out()


# ── Priority Resolver ──────────────────────────────────────

class TestPriorityResolver:
    def test_p0_always_allowed(self):
        pr = PriorityResolver()
        d = pr.resolve(Priority.P0)
        assert d.verdict == Verdict.ALLOW

    def test_p1_always_allowed(self):
        pr = PriorityResolver()
        d = pr.resolve(Priority.P1)
        assert d.verdict == Verdict.ALLOW

    def test_p3_needs_confirmation(self):
        pr = PriorityResolver()
        d = pr.resolve(Priority.P3)
        assert d.verdict == Verdict.NEEDS_CONFIRMATION

    def test_p4_needs_confirmation_and_token(self):
        pr = PriorityResolver()
        d = pr.resolve(Priority.P4)
        assert d.verdict == Verdict.NEEDS_CONFIRMATION
        assert d.requires_token

    def test_from_event_type_maps_user_message_to_p0(self):
        assert Priority.from_event_type("user_message") == Priority.P0

    def test_from_event_type_maps_maintenance_to_p2(self):
        assert Priority.from_event_type("maintenance") == Priority.P2

    def test_from_event_type_maps_reminder_to_p3(self):
        assert Priority.from_event_type("reminder_trigger") == Priority.P3


# ── Token Manager ──────────────────────────────────────────

class TestTokenManager:
    def test_issue_and_validate(self):
        tm = TokenManager()
        token = tm.issue("task-1", "code", ttl_seconds=60, budget_tokens=10)
        d = tm.validate(token.token_id)
        assert d.verdict == Verdict.ALLOW

    def test_validate_nonexistent_token(self):
        tm = TokenManager()
        d = tm.validate("nonexistent")
        assert d.verdict == Verdict.DENY

    def test_validate_expired_token(self):
        tm = TokenManager()
        token = tm.issue("task-2", "file", ttl_seconds=-1)
        d = tm.validate(token.token_id)
        assert d.verdict == Verdict.DENY

    def test_consume_budget(self):
        tm = TokenManager()
        token = tm.issue("task-3", "api", budget_tokens=5)
        assert tm.consume_budget(token.token_id, 3)
        assert not tm.consume_budget(token.token_id, 3)  # would exceed

    def test_revoke(self):
        tm = TokenManager()
        token = tm.issue("task-4", "browser", ttl_seconds=300)
        tm.revoke(token.token_id)
        d = tm.validate(token.token_id)
        assert d.verdict == Verdict.DENY

    def test_cleanup_removes_expired(self):
        tm = TokenManager()
        token1 = tm.issue("task-5", "comms", ttl_seconds=300)
        token2 = tm.issue("task-6", "comms", ttl_seconds=300)
        assert tm.get_state()["active_tokens"] == 2
        # force expire both
        token1.created_at = 0
        token2.created_at = 0
        assert tm.cleanup_expired() == 2
        assert tm.get_state()["active_tokens"] == 0


# ── Policy Engine Integration ──────────────────────────────

class TestPolicyEngineIntegration:
    def test_user_message_allowed_in_dormant(self):
        pe = PolicyEngine()
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict == Verdict.ALLOW

    def test_p4_denied_in_dormant(self):
        pe = PolicyEngine()
        d = pe.evaluate("system_tick", {"source": "scheduler"})
        # P4 in DORMANT → state guard denies
        assert d.verdict in (Verdict.DENY, Verdict.NEEDS_CONFIRMATION)

    def test_transition_to_quarantine_then_blocked(self):
        pe = PolicyEngine()
        pe.transition("violation_detected")
        assert pe.state_machine.current == State.QUARANTINED
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict == Verdict.DENY

    def test_get_state(self):
        pe = PolicyEngine()
        state = pe.get_state()
        assert "state_machine" in state
        assert "tokens" in state
        assert state["state_machine"]["current"] == "dormant"

    def test_default_policy_engine_works_without_config(self):
        pe = PolicyEngine()
        assert pe.state_machine.current == State.DORMANT
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict == Verdict.ALLOW
