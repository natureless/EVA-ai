"""Pure priority policies and the bounded global-workspace contract.

These policies rank already admitted work. They cannot authorize tools, add
events, mutate goals, or override downstream capacity and execution checks.
Inputs contain runtime measurements and event metadata, never payload priority
or permission claims.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


@dataclass(frozen=True)
class AttentionContext:
    now: float
    resource_pressure: float
    attention_activation: float


@dataclass(frozen=True)
class Candidate:
    event_id: str
    event_type: str
    goal_version: int
    summary: str
    accepted_at: float
    order: int


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: float


class ValuePolicy(Protocol):
    """Return a finite engineering priority; it is not a probability."""

    def score(self, candidate: Candidate, context: AttentionContext) -> float: ...


class AttentionPolicy(Protocol):
    """Choose unique admitted event IDs, in order, within workspace capacity."""

    def select(
        self, candidates: tuple[RankedCandidate, ...], capacity: int
    ) -> tuple[str, ...]: ...


class ResourceAwareValuePolicy:
    def score(self, candidate: Candidate, context: AttentionContext) -> float:
        base = {
            "user_message": 1.0,
            "reminder_trigger": 0.8,
            "maintenance": 0.2,
            "system_tick": 0.05,
        }.get(candidate.event_type, 0.5)
        modulation = 0.0
        if candidate.event_type == "maintenance":
            modulation = 2.0 * context.resource_pressure * context.attention_activation
        aging = min(2.0, max(0.0, context.now - candidate.accepted_at) / 30.0)
        return base + modulation + aging


class HighestValueAttentionPolicy:
    def select(
        self, candidates: tuple[RankedCandidate, ...], capacity: int
    ) -> tuple[str, ...]:
        ranked = sorted(
            candidates, key=lambda item: (-item.score, item.candidate.order)
        )
        return tuple(item.candidate.event_id for item in ranked[:capacity])


class GlobalWorkspace:
    """Commit selections only after validating the complete policy result.

    Both candidate and score records are immutable. Snapshots are fresh objects;
    policy callers cannot alter an already selected candidate's goal version.
    Invalid results preserve the preceding workspace and raise for the owner to
    handle, rather than silently dispatching an unvalidated alternative.
    """

    def __init__(
        self,
        capacity: int,
        *,
        value_policy: ValuePolicy | None = None,
        attention_policy: AttentionPolicy | None = None,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("workspace capacity must be a positive integer")
        self.capacity = capacity
        self.value_policy = (
            value_policy if value_policy is not None else ResourceAwareValuePolicy()
        )
        self.attention_policy = (
            attention_policy
            if attention_policy is not None
            else HighestValueAttentionPolicy()
        )
        self._selected: tuple[RankedCandidate, ...] = ()

    @property
    def first(self) -> Candidate | None:
        return self._selected[0].candidate if self._selected else None

    def select(
        self, candidates: tuple[Candidate, ...], context: AttentionContext
    ) -> None:
        by_id: dict[str, RankedCandidate] = {}
        for candidate in candidates:
            if candidate.event_id in by_id:
                raise ValueError("duplicate workspace candidate")
            score = self.value_policy.score(candidate, context)
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ValueError("value policy must return a finite numeric score")
            by_id[candidate.event_id] = RankedCandidate(candidate, float(score))
        selected = self.attention_policy.select(tuple(by_id.values()), self.capacity)
        if not isinstance(selected, tuple) or len(selected) > self.capacity:
            raise ValueError(
                "attention policy must return a bounded tuple of event IDs"
            )
        if any(not isinstance(event_id, str) for event_id in selected):
            raise ValueError("attention policy event IDs must be strings")
        if len(set(selected)) != len(selected) or any(
            event_id not in by_id for event_id in selected
        ):
            raise ValueError("attention policy selected duplicate or unadmitted events")
        self._selected = tuple(by_id[event_id] for event_id in selected)

    def clear(self) -> None:
        self._selected = ()

    def snapshot(self) -> list[dict]:
        return [
            {
                "event_id": entry.candidate.event_id,
                "event_type": entry.candidate.event_type,
                "goal_version": entry.candidate.goal_version,
                "score": entry.score,
                "summary": entry.candidate.summary,
                "epistemic_status": "candidate",
                "status": "queued",
            }
            for entry in self._selected
        ]
