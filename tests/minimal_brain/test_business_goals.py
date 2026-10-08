"""GOL-01 business goal lifecycle tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from pydantic import ValidationError

from packages.minimal_brain.business_goals import (
    BusinessGoal,
    BusinessGoalConflict,
    BusinessGoalLedger,
    BusinessGoalStatus,
    GoalCriterion,
)


def goal(**kwargs):
    values = {
        "description": "verify deployment",
        "source_event_id": "evt-goal",
        "success_conditions": [
            GoalCriterion(field="checks.ready", operator="equals", expected=True)
        ],
        "failure_conditions": [
            GoalCriterion(field="checks.blocked", operator="truthy")
        ],
    }
    values.update(kwargs)
    return BusinessGoal(**values)


def test_goal_does_not_complete_from_event_acceptance_alone():
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    assert created.status is BusinessGoalStatus.QUEUED
    active = ledger.transition(
        created.goal_id, BusinessGoalStatus.ACTIVE, expected_version=1
    )
    pending = ledger.observe(
        active.goal_id, {"checks": {"ready": False}}, expected_version=active.version
    )

    assert pending.status is BusinessGoalStatus.PENDING_VERIFICATION
    assert pending.version == 3


def test_success_and_failure_predicates_are_explicit():
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    active = ledger.transition(created.goal_id, "active")
    completed = ledger.observe(
        active.goal_id, {"checks": {"ready": True}}, expected_version=active.version
    )
    assert completed.status is BusinessGoalStatus.COMPLETED

    failed_goal = ledger.create(goal(source_event_id="evt-failed"))
    failed_active = ledger.transition(failed_goal.goal_id, "active")
    failed = ledger.observe(
        failed_active.goal_id,
        {"checks": {"blocked": "policy"}},
        expected_version=failed_active.version,
    )
    assert failed.status is BusinessGoalStatus.FAILED


def test_dependencies_and_optimistic_version_are_enforced():
    ledger = BusinessGoalLedger()
    prerequisite = ledger.create(goal(source_event_id="evt-prereq"))
    dependent = ledger.create(
        goal(source_event_id="evt-dependent", dependencies=[prerequisite.goal_id])
    )
    with pytest.raises(BusinessGoalConflict, match="dependencies"):
        ledger.transition(dependent.goal_id, "active")

    with pytest.raises(BusinessGoalConflict, match="version"):
        ledger.transition(prerequisite.goal_id, "active", expected_version=99)

    active = ledger.transition(prerequisite.goal_id, "active")
    done = ledger.observe(
        active.goal_id, {"checks": {"ready": True}}, expected_version=active.version
    )
    assert done.status is BusinessGoalStatus.COMPLETED
    assert (
        ledger.transition(dependent.goal_id, "active").status
        is BusinessGoalStatus.ACTIVE
    )


def test_cancel_and_restart_interrupt_running_work():
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    active = ledger.transition(created.goal_id, "active")
    cancelled = ledger.transition(
        active.goal_id, "cancelled", expected_version=active.version
    )
    assert cancelled.status is BusinessGoalStatus.CANCELLED

    other = ledger.create(goal(source_event_id="evt-restart"))
    running = ledger.transition(other.goal_id, "active")
    restored = BusinessGoalLedger()
    after_restart = restored.restore(ledger.snapshot())
    restored_goal = next(
        item for item in after_restart if item.goal_id == running.goal_id
    )
    assert restored_goal.status is BusinessGoalStatus.INTERRUPTED
    assert restored_goal.version == running.version + 1


def test_observation_cannot_mutate_ledger_input_or_returned_copy():
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    observation = {"checks": {"ready": False}}
    active = ledger.transition(created.goal_id, "active")
    pending = ledger.observe(
        active.goal_id, observation, expected_version=active.version
    )
    observation["checks"]["ready"] = True
    pending.last_observation["checks"]["ready"] = True

    assert ledger.get(active.goal_id).last_observation["checks"]["ready"] is False


@pytest.mark.parametrize("status", ["active", "pending_verification"])
def test_completion_cannot_bypass_success_conditions(status):
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    ledger.transition(created.goal_id, "active")
    if status == "pending_verification":
        ledger.transition(created.goal_id, status)
    before = ledger.snapshot()
    with pytest.raises(BusinessGoalConflict, match="invalid goal transition"):
        ledger.transition(
            created.goal_id, "completed", evidence={"claimed_success": True}
        )
    assert ledger.snapshot() == before


@pytest.mark.parametrize(
    "overrides", [{"status": "completed"}, {"status": "active"}, {"version": 2}]
)
def test_create_cannot_import_a_running_or_finished_goal(overrides):
    ledger = BusinessGoalLedger()
    with pytest.raises(BusinessGoalConflict, match="new goals"):
        ledger.create(goal(**overrides))
    assert ledger.snapshot() == []


def test_missing_success_conditions_and_conflicting_observations_do_not_complete():
    ledger = BusinessGoalLedger()
    empty = ledger.create(goal(success_conditions=[]))
    ledger.transition(empty.goal_id, "active")
    assert (
        ledger.observe(empty.goal_id, {"checks": {"ready": True}}).status
        is BusinessGoalStatus.PENDING_VERIFICATION
    )
    conflict = ledger.create(goal())
    ledger.transition(conflict.goal_id, "active")
    assert (
        ledger.observe(
            conflict.goal_id, {"checks": {"ready": True, "blocked": True}}
        ).status
        is BusinessGoalStatus.FAILED
    )


@pytest.mark.parametrize("revision", [-1, True, 1.5, "2"])
def test_invalid_observation_revision_leaves_entire_record_unchanged(revision):
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    ledger.transition(created.goal_id, "active")
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="state_revision"):
        ledger.observe(
            created.goal_id,
            {"checks": {"ready": True}},
            state_revision=revision,
            evidence={"ref": "x"},
        )
    assert ledger.snapshot() == before


@pytest.mark.parametrize("operation", ["observe", "transition"])
def test_invalid_evidence_does_not_partially_commit(operation):
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    ledger.transition(created.goal_id, "active")
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="evidence"):
        if operation == "observe":
            ledger.observe(
                created.goal_id, {"checks": {"ready": True}}, evidence=["bad"]
            )
        else:
            ledger.transition(created.goal_id, "cancelled", evidence=["bad"])
    assert ledger.snapshot() == before


@pytest.mark.parametrize("revision", [3, None])
def test_observations_cannot_regress_or_drop_state_revision(revision):
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    ledger.transition(created.goal_id, "active")
    pending = ledger.observe(
        created.goal_id, {"checks": {"ready": False}}, state_revision=4
    )
    before = ledger.snapshot()
    with pytest.raises(BusinessGoalConflict, match="state_revision"):
        ledger.observe(
            created.goal_id, {"checks": {"ready": True}}, state_revision=revision
        )
    assert ledger.snapshot() == before
    assert (
        ledger.observe(
            created.goal_id,
            {"checks": {"ready": True}},
            state_revision=4,
            expected_version=pending.version,
        ).status
        is BusinessGoalStatus.COMPLETED
    )


@pytest.mark.parametrize("tail", ["duplicate", "invalid", "existing"])
def test_restore_failure_is_atomic_including_existing_records(tail):
    ledger = BusinessGoalLedger()
    existing = ledger.create(goal())
    raw = goal().snapshot()
    bad = (
        raw if tail == "duplicate" else {} if tail == "invalid" else existing.snapshot()
    )
    before = ledger.snapshot()
    with pytest.raises((BusinessGoalConflict, ValidationError)):
        ledger.restore([raw, bad])
    assert ledger.snapshot() == before


def test_restore_copies_nested_data_in_both_directions():
    raw = goal(
        evidence=[{"values": [1]}], last_observation={"checks": {"ready": False}}
    ).snapshot()
    ledger = BusinessGoalLedger()
    result = ledger.restore([raw])[0]
    raw["evidence"][0]["values"].append(2)
    result.last_observation["checks"]["ready"] = True
    saved = ledger.get(result.goal_id)
    assert saved.evidence == [{"values": [1]}]
    assert saved.last_observation == {"checks": {"ready": False}}


def test_two_writers_with_same_version_cannot_both_commit():
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    active = ledger.transition(created.goal_id, "active")
    barrier = Barrier(2)

    def update(status):
        barrier.wait(timeout=5)
        try:
            return ledger.transition(
                active.goal_id, status, expected_version=active.version
            ).status.value
        except BusinessGoalConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["pending_verification", "cancelled"]))
    assert results.count("conflict") == 1
    assert ledger.get(active.goal_id).version == active.version + 1


@pytest.mark.parametrize(
    "state", ["queued", "active", "pending_verification", "interrupted"]
)
def test_deadline_blocks_late_success_and_expires_once(state):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    ledger = BusinessGoalLedger(clock=lambda: now)
    created = ledger.create(goal(deadline=now + timedelta(seconds=1)))
    if state != "queued":
        ledger.transition(created.goal_id, "active")
    if state not in {"queued", "active"}:
        ledger.transition(created.goal_id, state)
    before = ledger.snapshot()
    now += timedelta(seconds=1)
    with pytest.raises(BusinessGoalConflict, match="deadline"):
        if state in {"queued", "interrupted"}:
            ledger.transition(created.goal_id, "active")
        else:
            ledger.observe(created.goal_id, {"checks": {"ready": True}})
    assert ledger.snapshot() == before
    expired = ledger.expire_due()
    assert len(expired) == 1
    assert expired[0].status is BusinessGoalStatus.EXPIRED
    assert expired[0].version == before[0]["version"] + 1
    assert ledger.expire_due() == []


def test_restore_expires_overdue_work_but_keeps_terminal_history():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    ledger = BusinessGoalLedger(clock=lambda: now)
    for status in [
        "queued",
        "active",
        "pending_verification",
        "interrupted",
        "completed",
        "cancelled",
    ]:
        raw = goal(status=status, deadline=now - timedelta(seconds=1)).snapshot()
        restored = ledger.restore([raw])[0]
        terminal = status in {"completed", "cancelled"}
        assert restored.status.value == (status if terminal else "expired")
        assert restored.version == (1 if terminal else 2)


def test_naive_deadlines_and_boolean_versions_are_rejected():
    with pytest.raises(ValidationError):
        goal(deadline=datetime(2026, 9, 25))
    with pytest.raises(ValidationError):
        goal(version=True)
    ledger = BusinessGoalLedger()
    created = ledger.create(goal())
    with pytest.raises(ValueError, match="expected_version"):
        ledger.transition(created.goal_id, "active", expected_version=True)
    assert ledger.get(created.goal_id).version == 1
