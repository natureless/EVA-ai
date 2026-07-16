"""Policy engine — enforces constitution.yaml rules at runtime.

Composes three subsystems:
- StateMachine: DORMANT / COMMANDED / SUPERVISED / QUARANTINED
- PriorityResolver: P0-P4 conflict resolution
- TokenManager: executor token lifecycle

All three are consulted by PolicyEngine.evaluate() on every cognition event.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time
from uuid import uuid4


# ── Enums ───────────────────────────────────────────────────

class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_CONFIRMATION = "needs_confirmation"
    QUARANTINE = "quarantine"


class State(str, Enum):
    DORMANT = "dormant"
    COMMANDED = "commanded"
    SUPERVISED = "supervised"
    QUARANTINED = "quarantined"


class Priority(int, Enum):
    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4

    @classmethod
    def from_event_type(cls, event_type: str, source: str = "") -> "Priority":
        if event_type == "user_message":
            return cls.P0
        if event_type == "maintenance":
            return cls.P2
        if event_type == "reminder_trigger":
            return cls.P3
        if event_type == "system_tick":
            return cls.P4
        return cls.P4


# ── Data classes ────────────────────────────────────────────

@dataclass
class PolicyDecision:
    verdict: Verdict
    reason: str = ""
    requires_token: bool = False
    token_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Token:
    token_id: str
    task_id: str
    executor_type: str
    scope: list[str]
    ttl_seconds: int
    budget_tokens: int
    created_at: float
    created_by: str
    consumed: int = 0

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    @property
    def budget_exhausted(self) -> bool:
        return self.consumed >= self.budget_tokens

    @property
    def valid(self) -> bool:
        return not self.expired and not self.budget_exhausted


# ── State Machine ───────────────────────────────────────────

DEFAULT_TRANSITIONS = {
    (State.DORMANT, "user_command"): State.COMMANDED,
    (State.DORMANT, "autonomy_granted"): State.SUPERVISED,
    (State.DORMANT, "scheduled_task"): State.COMMANDED,
    (State.COMMANDED, "command_completed"): State.DORMANT,
    (State.COMMANDED, "command_failed"): State.DORMANT,
    (State.COMMANDED, "timeout"): State.DORMANT,
    (State.SUPERVISED, "budget_exhausted"): State.DORMANT,
    (State.SUPERVISED, "time_window_expired"): State.DORMANT,
    (State.SUPERVISED, "user_revoke"): State.DORMANT,
    (State.QUARANTINED, "manual_intervention"): State.DORMANT,
}


class StateMachine:
    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        sm_cfg = cfg.get("state_machine", {})
        self._transitions: dict[tuple[State, str], State] = dict(DEFAULT_TRANSITIONS)
        self._state: State = State.DORMANT
        self._history: list[dict[str, Any]] = []
        self._timeout_minutes: dict[State, int] = {
            State.COMMANDED: sm_cfg.get("states", {}).get("Commanded", {}).get("timeout_minutes", 30),
            State.SUPERVISED: sm_cfg.get("states", {}).get("Supervised", {}).get("max_session_minutes", 120),
        }
        self._state_since = time.time()

    @property
    def current(self) -> State:
        return self._state

    @property
    def state_since(self) -> float:
        return self._state_since

    def transition(self, trigger: str) -> PolicyDecision:
        key = (self._state, trigger)
        new_state = self._transitions.get(key)

        # wildcard: any state → QUARANTINED on violation
        if new_state is None and trigger == "violation_detected":
            new_state = State.QUARANTINED

        if new_state is None:
            return PolicyDecision(
                verdict=Verdict.DENY,
                reason=f"invalid transition: {self._state.value} + {trigger}",
            )

        old = self._state
        self._state = new_state
        self._state_since = time.time()
        self._history.append({
            "from": old.value,
            "to": new_state.value,
            "trigger": trigger,
            "ts": time.time(),
        })
        if len(self._history) > 50:
            self._history = self._history[-50:]

        return PolicyDecision(
            verdict=Verdict.ALLOW,
            reason=f"state: {old.value} → {new_state.value}",
        )

    def can_execute(self, priority: Priority) -> PolicyDecision:
        if self._state == State.QUARANTINED:
            return PolicyDecision(
                verdict=Verdict.DENY,
                reason="quarantined — manual intervention required",
            )
        if self._state == State.SUPERVISED and priority == Priority.P4:
            return PolicyDecision(
                verdict=Verdict.NEEDS_CONFIRMATION,
                reason="P4 actions require confirmation in supervised mode",
            )
        if self._state == State.DORMANT and priority >= Priority.P3:
            return PolicyDecision(
                verdict=Verdict.DENY,
                reason=f"state {self._state.value} does not allow priority {priority.name}",
            )
        return PolicyDecision(verdict=Verdict.ALLOW, reason="state permits action")

    def is_timed_out(self) -> bool:
        if self._state not in self._timeout_minutes:
            return False
        elapsed = (time.time() - self._state_since) / 60
        return elapsed > self._timeout_minutes[self._state]

    def get_state(self) -> dict[str, Any]:
        return {
            "current": self._state.value,
            "since": self._state_since,
            "elapsed_seconds": round(time.time() - self._state_since, 1),
            "history": self._history[-10:],
            "history_size": len(self._history),
        }


# ── Priority Resolver ───────────────────────────────────────

class PriorityResolver:
    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}

    def resolve(
        self, incoming: Priority, current: Priority | None = None
    ) -> PolicyDecision:
        # P1 (hard safety) always enforced
        if incoming == Priority.P1:
            return PolicyDecision(
                verdict=Verdict.ALLOW,
                reason="P1 safety rules always enforced",
            )
        # P0 (user command) overrides everything except P1
        if incoming == Priority.P0:
            return PolicyDecision(
                verdict=Verdict.ALLOW,
                reason="P0 user command — highest authority",
            )
        # P2 (scheduled) allowed unless interrupted by P0
        if incoming == Priority.P2:
            return PolicyDecision(
                verdict=Verdict.ALLOW,
                reason="P2 scheduled task — within granted scope",
            )
        # P3 (suggestion) needs confirmation
        if incoming == Priority.P3:
            return PolicyDecision(
                verdict=Verdict.NEEDS_CONFIRMATION,
                reason="P3 suggestion — requires user approval",
            )
        # P4 (autonomous exploration) — severely restricted
        if incoming == Priority.P4:
            return PolicyDecision(
                verdict=Verdict.NEEDS_CONFIRMATION,
                reason="P4 autonomous action — requires supervised mode + budget",
                requires_token=True,
            )
        return PolicyDecision(verdict=Verdict.ALLOW, reason="default allow")


# ── Token Manager ───────────────────────────────────────────

class TokenManager:
    def __init__(self, config: dict | None = None) -> None:
        self._tokens: dict[str, Token] = {}
        self._max_tokens = 50

    def issue(
        self,
        task_id: str,
        executor_type: str,
        scope: list[str] | None = None,
        ttl_seconds: int = 300,
        budget_tokens: int = 100,
        created_by: str = "P0",
    ) -> Token:
        if len(self._tokens) >= self._max_tokens:
            self.cleanup_expired()  # make room
        if len(self._tokens) >= self._max_tokens:
            oldest = min(self._tokens.values(), key=lambda t: t.created_at)
            del self._tokens[oldest.token_id]

        token = Token(
            token_id=f"tok_{uuid4().hex[:12]}",
            task_id=task_id,
            executor_type=executor_type,
            scope=scope or [],
            ttl_seconds=ttl_seconds,
            budget_tokens=budget_tokens,
            created_at=time.time(),
            created_by=created_by,
        )
        self._tokens[token.token_id] = token
        return token

    def validate(self, token_id: str) -> PolicyDecision:
        token = self._tokens.get(token_id)
        if token is None:
            return PolicyDecision(verdict=Verdict.DENY, reason="token not found")
        if token.expired:
            del self._tokens[token_id]
            return PolicyDecision(verdict=Verdict.DENY, reason="token expired")
        if token.budget_exhausted:
            return PolicyDecision(verdict=Verdict.DENY, reason="token budget exhausted")
        return PolicyDecision(verdict=Verdict.ALLOW, reason="token valid")

    def consume_budget(self, token_id: str, amount: int = 1) -> bool:
        token = self._tokens.get(token_id)
        if token is None or token.expired:
            return False
        if token.consumed + amount > token.budget_tokens:
            return False
        token.consumed += amount
        return True

    def revoke(self, token_id: str) -> None:
        self._tokens.pop(token_id, None)

    def cleanup_expired(self) -> int:
        expired = [tid for tid, t in self._tokens.items() if t.expired]
        for tid in expired:
            del self._tokens[tid]
        return len(expired)

    def get_state(self) -> dict[str, Any]:
        return {
            "active_tokens": len(self._tokens),
            "tokens": [
                {
                    "token_id": t.token_id,
                    "task_id": t.task_id,
                    "executor_type": t.executor_type,
                    "scope": t.scope,
                    "budget_remaining": t.budget_tokens - t.consumed,
                    "ttl_remaining_seconds": max(0, int(t.ttl_seconds - (time.time() - t.created_at))),
                    "valid": t.valid,
                }
                for t in self._tokens.values()
            ],
        }


# ── Policy Engine ───────────────────────────────────────────

class PolicyEngine:
    """Unified policy enforcement point.

    Usage in cognition loop::

        decision = policy.evaluate("user_message", context={"source": "user"})
        if decision.verdict == Verdict.DENY:
            return  # blocked
        if decision.verdict == Verdict.NEEDS_CONFIRMATION:
            ...  # request confirmation
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.state_machine = StateMachine(cfg)
        self.priority_resolver = PriorityResolver(cfg)
        self.token_manager = TokenManager(cfg)
        self._config = cfg

    def evaluate(
        self,
        event_type: str,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        ctx = context or {}
        source = str(ctx.get("source", ""))

        # 1. Check timeout
        if self.state_machine.is_timed_out():
            self.state_machine.transition("timeout")

        # 2. State machine guard
        priority = Priority.from_event_type(event_type, source)
        state_decision = self.state_machine.can_execute(priority)
        if state_decision.verdict != Verdict.ALLOW:
            return state_decision

        # 3. Priority resolution
        priority_decision = self.priority_resolver.resolve(priority)
        if priority_decision.verdict == Verdict.DENY:
            return priority_decision

        # 4. Token requirement
        if priority_decision.requires_token:
            token = self.token_manager.issue(
                task_id=ctx.get("task_id", f"task_{uuid4().hex[:8]}"),
                executor_type=ctx.get("executor_type", "default"),
                scope=ctx.get("scope", []),
                ttl_seconds=ctx.get("ttl_seconds", 300),
                budget_tokens=ctx.get("budget_tokens", 100),
                created_by=priority.name,
            )
            priority_decision.token_id = token.token_id

        return priority_decision

    def transition(self, trigger: str) -> PolicyDecision:
        return self.state_machine.transition(trigger)

    def get_state(self) -> dict[str, Any]:
        return {
            "state_machine": self.state_machine.get_state(),
            "tokens": self.token_manager.get_state(),
        }

    def shutdown(self) -> None:
        self.token_manager.cleanup_expired()
