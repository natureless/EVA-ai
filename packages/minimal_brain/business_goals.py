"""Explicit business-goal lifecycle with observable completion predicates."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class BusinessGoalStatus(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    EXPIRED = "expired"


class BusinessGoalConflict(ValueError):
    """A stale version or invalid goal transition cannot mutate the goal."""


class GoalCriterion(BaseModel):
    """A deterministic predicate over an observed result dictionary."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(default_factory=lambda: f"criterion_{uuid4().hex[:8]}")
    field: str = Field(min_length=1, max_length=256)
    operator: Literal["equals", "not_equals", "contains", "gte", "lte", "truthy"] = (
        "equals"
    )
    expected: Any = None

    def matches(self, observation: dict[str, Any]) -> bool:
        value: Any = observation
        for part in self.field.split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        if self.operator == "truthy":
            return bool(value)
        if self.operator == "equals":
            return value == self.expected
        if self.operator == "not_equals":
            return value != self.expected
        if self.operator == "contains":
            try:
                return self.expected in value
            except TypeError:
                return False
        if self.operator == "gte":
            try:
                return value >= self.expected
            except TypeError:
                return False
        if self.operator == "lte":
            try:
                return value <= self.expected
            except TypeError:
                return False
        return False


class BusinessGoal(BaseModel):
    """A goal whose lifecycle is independent from one event receipt."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(default_factory=lambda: f"goal_{uuid4().hex}")
    description: str = Field(min_length=1, max_length=1000)
    source_event_id: str = Field(min_length=1, max_length=256)
    status: BusinessGoalStatus = BusinessGoalStatus.QUEUED
    version: int = Field(default=1, ge=1, strict=True)
    dependencies: list[str] = Field(default_factory=list)
    success_conditions: list[GoalCriterion] = Field(default_factory=list)
    failure_conditions: list[GoalCriterion] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    deadline: AwareDatetime | None = None
    state_revision: int | None = Field(default=None, ge=0, strict=True)
    plan_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    last_observation: dict[str, Any] = Field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


_TRANSITIONS: dict[BusinessGoalStatus, set[BusinessGoalStatus]] = {
    BusinessGoalStatus.QUEUED: {
        BusinessGoalStatus.ACTIVE,
        BusinessGoalStatus.CANCELLED,
        BusinessGoalStatus.EXPIRED,
    },
    BusinessGoalStatus.ACTIVE: {
        BusinessGoalStatus.PENDING_VERIFICATION,
        BusinessGoalStatus.FAILED,
        BusinessGoalStatus.CANCELLED,
        BusinessGoalStatus.INTERRUPTED,
        BusinessGoalStatus.EXPIRED,
    },
    BusinessGoalStatus.PENDING_VERIFICATION: {
        BusinessGoalStatus.ACTIVE,
        BusinessGoalStatus.FAILED,
        BusinessGoalStatus.CANCELLED,
        BusinessGoalStatus.EXPIRED,
    },
    BusinessGoalStatus.INTERRUPTED: {
        BusinessGoalStatus.ACTIVE,
        BusinessGoalStatus.CANCELLED,
        BusinessGoalStatus.EXPIRED,
    },
}


class BusinessGoalLedger:
    """Process-local ledger; the caller owns observation authenticity and persistence."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._goals: dict[str, BusinessGoal] = {}
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, goal: BusinessGoal) -> BusinessGoal:
        with self._lock:
            candidate = BusinessGoal.model_validate(deepcopy(goal.model_dump()))
            if candidate.goal_id in self._goals:
                raise BusinessGoalConflict("goal already exists")
            if (
                candidate.status is not BusinessGoalStatus.QUEUED
                or candidate.version != 1
            ):
                raise BusinessGoalConflict(
                    "new goals must be queued at version 1; use restore for saved goals"
                )
            if candidate.goal_id in candidate.dependencies:
                raise BusinessGoalConflict("goal cannot depend on itself")
            self._goals[candidate.goal_id] = candidate
            return self._copy(candidate)

    def get(self, goal_id: str) -> BusinessGoal | None:
        with self._lock:
            goal = self._goals.get(goal_id)
            return self._copy(goal) if goal is not None else None

    def transition(
        self,
        goal_id: str,
        status: BusinessGoalStatus | str,
        *,
        expected_version: int | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> BusinessGoal:
        with self._lock:
            goal = self._require(goal_id)
            status = BusinessGoalStatus(status)
            self._check_version(goal, expected_version)
            if status not in _TRANSITIONS.get(goal.status, set()):
                raise BusinessGoalConflict(
                    f"invalid goal transition: {goal.status.value} -> {status.value}"
                )
            now = self._now()
            if status not in {BusinessGoalStatus.CANCELLED, BusinessGoalStatus.EXPIRED}:
                self._check_deadline(goal, now)
            if status is BusinessGoalStatus.ACTIVE and not self._dependencies_complete(
                goal
            ):
                raise BusinessGoalConflict("goal dependencies are not completed")
            candidate = self._copy(goal)
            candidate.status = status
            self._append_evidence(candidate, evidence)
            return self._commit(candidate, now)

    def observe(
        self,
        goal_id: str,
        observation: dict[str, Any],
        *,
        expected_version: int | None = None,
        evidence: dict[str, Any] | None = None,
        state_revision: int | None = None,
    ) -> BusinessGoal:
        """Evaluate caller-supplied observations, not independently verified facts."""
        if not isinstance(observation, dict):
            raise ValueError("observation must be a dictionary")
        with self._lock:
            goal = self._require(goal_id)
            self._check_version(goal, expected_version)
            if goal.status not in {
                BusinessGoalStatus.ACTIVE,
                BusinessGoalStatus.PENDING_VERIFICATION,
            }:
                raise BusinessGoalConflict(
                    f"goal is not observable in status {goal.status.value}"
                )
            now = self._now()
            self._check_deadline(goal, now)
            if state_revision is not None:
                if type(state_revision) is not int or state_revision < 0:
                    raise ValueError("state_revision must be a non-negative integer")
                if (
                    goal.state_revision is not None
                    and state_revision < goal.state_revision
                ):
                    raise BusinessGoalConflict("observation state_revision is stale")
            elif goal.state_revision is not None:
                raise BusinessGoalConflict("observation requires state_revision")
            candidate = self._copy(goal)
            candidate.last_observation = deepcopy(observation)
            if state_revision is not None:
                candidate.state_revision = state_revision
            self._append_evidence(candidate, evidence)
            candidate.status = self._evaluate_status(
                candidate, candidate.last_observation
            )
            return self._commit(candidate, now)

    def restore(
        self, snapshots: list[dict[str, Any]], *, recover: bool = True
    ) -> list[BusinessGoal]:
        """Atomically import trusted snapshots; never replay actions or infer success."""
        with self._lock:
            now = self._now()
            staged: dict[str, BusinessGoal] = {}
            for raw in snapshots:
                goal = BusinessGoal.model_validate(deepcopy(raw))
                if goal.goal_id in self._goals or goal.goal_id in staged:
                    raise BusinessGoalConflict("duplicate restored goal")
                if goal.goal_id in goal.dependencies:
                    raise BusinessGoalConflict("goal cannot depend on itself")
                if recover and self._is_due(goal, now):
                    goal.status = BusinessGoalStatus.EXPIRED
                    goal.version += 1
                    goal.updated_at = now
                elif recover and goal.status in {
                    BusinessGoalStatus.ACTIVE,
                    BusinessGoalStatus.PENDING_VERIFICATION,
                }:
                    goal.status = BusinessGoalStatus.INTERRUPTED
                    goal.version += 1
                    goal.updated_at = now
                staged[goal.goal_id] = goal
            result = [self._copy(goal) for goal in staged.values()]
            self._goals.update(staged)
            return result

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [goal.snapshot() for goal in self._goals.values()]

    def expire_due(self) -> list[BusinessGoal]:
        """Expire overdue nonterminal goals when explicitly called by the owner."""
        with self._lock:
            now = self._now()
            expired = []
            for goal in list(self._goals.values()):
                if self._is_due(goal, now):
                    candidate = self._copy(goal)
                    candidate.status = BusinessGoalStatus.EXPIRED
                    expired.append(self._commit(candidate, now))
            return expired

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("clock must return a timezone-aware datetime")
        return now

    @staticmethod
    def _is_due(goal: BusinessGoal, now: datetime) -> bool:
        return (
            goal.status in _TRANSITIONS
            and goal.deadline is not None
            and now >= goal.deadline
        )

    @staticmethod
    def _check_deadline(goal: BusinessGoal, now: datetime) -> None:
        if BusinessGoalLedger._is_due(goal, now):
            raise BusinessGoalConflict("goal deadline elapsed; call expire_due")

    @staticmethod
    def _append_evidence(goal: BusinessGoal, evidence: dict[str, Any] | None) -> None:
        if evidence is not None:
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be a dictionary")
            goal.evidence = (goal.evidence + [deepcopy(evidence)])[-50:]

    def _commit(self, candidate: BusinessGoal, now: datetime) -> BusinessGoal:
        candidate.version += 1
        candidate.updated_at = now
        result = self._copy(candidate)
        self._goals[candidate.goal_id] = candidate
        return result

    def _evaluate_status(
        self, goal: BusinessGoal, observation: dict[str, Any]
    ) -> BusinessGoalStatus:
        if any(condition.matches(observation) for condition in goal.failure_conditions):
            return BusinessGoalStatus.FAILED
        if goal.success_conditions and all(
            condition.matches(observation) for condition in goal.success_conditions
        ):
            return BusinessGoalStatus.COMPLETED
        return BusinessGoalStatus.PENDING_VERIFICATION

    def _dependencies_complete(self, goal: BusinessGoal) -> bool:
        return all(
            self._goals.get(dependency) is not None
            and self._goals[dependency].status is BusinessGoalStatus.COMPLETED
            for dependency in goal.dependencies
        )

    def _require(self, goal_id: str) -> BusinessGoal:
        if goal_id not in self._goals:
            raise KeyError(goal_id)
        return self._goals[goal_id]

    @staticmethod
    def _check_version(goal: BusinessGoal, expected_version: int | None) -> None:
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise ValueError("expected_version must be a positive integer")
        if expected_version is not None and expected_version != goal.version:
            raise BusinessGoalConflict("goal version changed")

    @staticmethod
    def _copy(goal: BusinessGoal | None) -> BusinessGoal:
        assert goal is not None
        return goal.model_copy(deep=True)


__all__ = [
    "BusinessGoal",
    "BusinessGoalConflict",
    "BusinessGoalLedger",
    "BusinessGoalStatus",
    "GoalCriterion",
]
