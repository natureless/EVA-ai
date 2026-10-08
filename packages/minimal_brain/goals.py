"""Versioned lifecycle for event-handling goals, owned by the coordinator.

Completion describes the stable processor's receipt, not success of an entire
user project or verification of its answer. Executing goals cannot be cancelled
through this ledger; actual execution ownership remains with the kernel.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from event.event_schema import Event


class GoalStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    INTERRUPTED = "interrupted"


_TRANSITIONS = {
    GoalStatus.QUEUED: frozenset(
        {
            GoalStatus.RUNNING,
            GoalStatus.CANCELLED,
            GoalStatus.EXPIRED,
            GoalStatus.INTERRUPTED,
        }
    ),
    GoalStatus.RUNNING: frozenset(
        {GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.INTERRUPTED}
    ),
}


class GoalConflict(ValueError):
    """A stale version or invalid transition cannot mutate a goal."""


@dataclass(frozen=True)
class GoalRecord:
    event_id: str
    correlation_id: str | None
    version: int
    status: GoalStatus
    summary: str
    source: str
    event_type: str
    kind: str = "handle_event"
    success_condition: str = "stable_processor_returns_success_receipt"

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


class GoalLedger:
    def __init__(self) -> None:
        self._records: OrderedDict[str, GoalRecord] = OrderedDict()

    def __getitem__(self, event_id: str) -> GoalRecord:
        return self._records[event_id]

    def get(self, event_id: str) -> GoalRecord | None:
        return self._records.get(event_id)

    def accept(self, event: Event) -> GoalRecord:
        if event.id in self._records:
            raise GoalConflict("goal already exists")
        goal = GoalRecord(
            event_id=event.id,
            correlation_id=event.correlation_id,
            version=1,
            status=GoalStatus.QUEUED,
            summary=str(event.payload.get("text", event.type))[:160],
            source=event.source,
            event_type=event.type,
        )
        self._records[event.id] = goal
        return goal

    def transition(
        self,
        event_id: str,
        status: GoalStatus | str,
        *,
        expected_version: int | None = None,
    ) -> GoalRecord:
        goal = self._records[event_id]
        status = GoalStatus(status)
        if expected_version is not None and expected_version != goal.version:
            raise GoalConflict("goal version changed")
        if status not in _TRANSITIONS.get(goal.status, frozenset()):
            raise GoalConflict(
                f"invalid goal transition: {goal.status.value} -> {status.value}"
            )
        updated = replace(goal, status=status, version=goal.version + 1)
        self._records[event_id] = updated
        return updated

    def restore(self, raw: dict[str, Any]) -> tuple[GoalRecord, bool]:
        if not isinstance(raw, dict):
            raise ValueError("invalid saved goal")
        for key in ("event_id", "summary", "source", "event_type"):
            if not isinstance(raw.get(key), str):
                raise ValueError(f"invalid saved goal {key}")
        version = raw.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("invalid saved goal version")
        correlation_id = raw.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise ValueError("invalid saved goal correlation_id")
        if (
            raw.get("kind", "handle_event") != "handle_event"
            or raw.get("success_condition", "stable_processor_returns_success_receipt")
            != "stable_processor_returns_success_receipt"
        ):
            raise ValueError("unsupported saved goal contract")
        if raw["event_id"] in self._records:
            raise GoalConflict("duplicate saved goal")
        goal = GoalRecord(
            event_id=raw["event_id"],
            correlation_id=correlation_id,
            version=version,
            status=GoalStatus(raw.get("status")),
            summary=raw["summary"][:160],
            source=raw["source"],
            event_type=raw["event_type"],
        )
        self._records[goal.event_id] = goal
        interrupted = goal.status in {GoalStatus.QUEUED, GoalStatus.RUNNING}
        if interrupted:
            goal = self.transition(goal.event_id, GoalStatus.INTERRUPTED)
        return goal, interrupted

    def trim(self, *, history_limit: int, protected: set[str]) -> None:
        for event_id in tuple(self._records):
            if len(self._records) <= history_limit + len(protected):
                break
            if event_id not in protected:
                del self._records[event_id]

    def resume_unclaimed(self, event: Event) -> GoalRecord:
        """Trusted startup proof only; no general terminal-to-running transition."""
        goal = self._records[event.id]
        if (
            goal.status is not GoalStatus.INTERRUPTED
            or goal.correlation_id != event.correlation_id
            or goal.source != event.source
            or goal.event_type != event.type
            or goal.summary != str(event.payload.get("text", event.type))[:160]
        ):
            raise GoalConflict("unclaimed request conflicts with restored goal")
        resumed = replace(goal, status=GoalStatus.QUEUED, version=goal.version + 1)
        self._records[event.id] = resumed
        return resumed

    def snapshot(self) -> list[dict[str, Any]]:
        return [goal.snapshot() for goal in self._records.values()]
