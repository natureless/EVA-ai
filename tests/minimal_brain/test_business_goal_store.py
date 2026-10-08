"""Persistence, recovery and canonical-receipt trust boundary regressions."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from packages.minimal_brain.business_goal_store import BusinessGoalStore
from packages.minimal_brain.business_goals import BusinessGoalConflict
from runtime.result_registry import ResultRegistry


def create(store, **kwargs):
    return store.create(
        **{
            "task_id": "task-a",
            "source_event_id": "event-a",
            "description": "check deployment",
            "success_conditions": [{"field": "checks.ready", "expected": True}],
            **kwargs,
        }
    )


def receipt(terminal="succeeded", **kwargs):
    return {
        "task_id": "task-a",
        "event_id": "event-a",
        "terminal_state": terminal,
        "ok": terminal == "succeeded",
        **kwargs,
    }


@pytest.fixture
def store(tmp_path):
    result = BusinessGoalStore(tmp_path / "eva.db")
    yield result
    result.close()


@pytest.mark.parametrize(
    "terminal,status",
    [
        ("succeeded", "pending_verification"),
        ("outcome_unknown", "interrupted"),
        ("failed", "failed"),
        ("rejected", "failed"),
        ("expired", "expired"),
    ],
)
def test_receipt_projection_is_durable_and_idempotent(
    store, tmp_path, terminal, status
):
    goal = create(store)
    payload = receipt(
        terminal,
        reply="goal complete",
        checks={"ready": True},
        review={"fact_verified": True},
    )
    store.record_receipt(payload)
    actual = store.get(goal["goal_id"])
    assert actual["status"] == status
    assert actual["verification_state"] == "not_verified"
    assert actual["processing_receipt"] == receipt(terminal)
    store.record_receipt(payload)
    assert store.get(goal["goal_id"]) == actual
    store.close()
    reopened = BusinessGoalStore(tmp_path / "eva.db")
    try:
        assert reopened.get(goal["goal_id"]) == actual
    finally:
        reopened.close()


def test_receipt_and_goal_commit_rollback_together(store):
    goal = create(store)
    store._conn.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON business_goal_receipts
                           BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_receipt(receipt())
    assert store.get(goal["goal_id"]) == goal
    store._conn.execute("DROP TRIGGER fail_receipt")
    store.record_receipt(receipt())
    assert store.get(goal["goal_id"])["status"] == "pending_verification"


def test_event_mismatch_and_inconsistent_receipt_cannot_mutate_goal(store):
    goal = create(store)
    for invalid in [
        receipt(event_id="different"),
        receipt(ok=False),
        receipt(terminal_state="completed"),
    ]:
        with pytest.raises(ValueError):
            store.record_receipt(invalid)
        assert store.get(goal["goal_id"]) == goal
    store.record_receipt(receipt())
    with pytest.raises(BusinessGoalConflict):
        store.record_receipt(receipt("failed"))
    assert store.get(goal["goal_id"])["status"] == "pending_verification"


def test_recovery_interrupts_work_without_actions_and_preserves_history(store):
    pending = create(store)
    store.record_receipt(receipt())
    cancelled = create(store, task_id="task-b", source_event_id="event-b")
    store.cancel(cancelled["goal_id"], expected_version=cancelled["version"])
    assert store.recover() == 1
    assert store.get(pending["goal_id"])["status"] == "interrupted"
    assert store.get(pending["goal_id"])["processing_receipt"] == receipt()
    assert store.get(cancelled["goal_id"])["status"] == "cancelled"
    assert store.recover() == 0


def test_unknown_schema_rolls_back_entire_recovery(store):
    first = create(store)
    second = create(store, task_id="b", source_event_id="b")
    store._conn.execute(
        "UPDATE business_goals SET schema_version=99 WHERE goal_id=?",
        (second["goal_id"],),
    )
    store._conn.commit()
    with pytest.raises(ValueError, match="schema"):
        store.recover()
    assert store.get(first["goal_id"]) == first


def test_two_connections_enforce_version_conflicts(store, tmp_path):
    goal = create(store)
    other = BusinessGoalStore(tmp_path / "eva.db")
    try:
        store.cancel(goal["goal_id"], expected_version=goal["version"])
        with pytest.raises(BusinessGoalConflict):
            other.cancel(goal["goal_id"], expected_version=goal["version"])
        store.record_receipt(receipt())
        assert other.get(goal["goal_id"])["status"] == "cancelled"
    finally:
        other.close()


def test_late_receipt_records_execution_but_goal_expires(tmp_path):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    store = BusinessGoalStore(tmp_path / "eva.db", clock=lambda: now)
    try:
        goal = create(store, deadline=now + timedelta(seconds=1))
        now += timedelta(seconds=1)
        store.record_receipt(receipt())
        view = store.get(goal["goal_id"])
        assert view["status"] == "expired" and view["processing_receipt"]["ok"]
        assert store.get(goal["goal_id"])["version"] == view["version"]
    finally:
        store.close()


def test_registered_ids_override_payload_and_observer_errors_keep_receipt(store):
    goal = create(store)
    registry = ResultRegistry(receipt_observer=store.record_receipt)
    registry.create("task-a", event_id="event-a")
    assert registry.fulfill("task-a", receipt(event_id="forged", task_id="forged"))
    assert store.get(goal["goal_id"])["processing_receipt"] == receipt()
    assert registry.stats()["receipt_observer_errors"] == 0


@pytest.mark.parametrize("started", [False, True])
def test_registry_expiration_reaches_persistent_goals(store, started):
    goal = create(store)
    now = [0.0]
    registry = ResultRegistry(
        clock=lambda: now[0],
        pending_timeout_sec=1,
        receipt_observer=store.record_receipt,
    )
    registry.create("task-a", event_id="event-a")
    if started:
        registry.begin("task-a", event_id="event-a")
    now[0] = 2.0
    assert registry.lookup("task-a")["state"] == "terminal"
    assert store.get(goal["goal_id"])["status"] == (
        "interrupted" if started else "expired"
    )


def test_callback_failure_keeps_receipt_for_reconciliation(store):
    goal = create(store)
    store._conn.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON business_goal_receipts
                           BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    registry = ResultRegistry(receipt_observer=store.record_receipt)
    registry.create("task-a", event_id="event-a")
    assert registry.fulfill("task-a", receipt())
    assert registry.stats()["receipt_observer_errors"] == 1
    assert store.get(goal["goal_id"]) == goal
    store._conn.execute("DROP TRIGGER fail_receipt")
    store.record_receipt(registry.peek("task-a"))
    assert store.get(goal["goal_id"])["status"] == "pending_verification"


def test_late_success_cannot_replace_a_deadline_receipt(store):
    goal = create(store)
    now = [0.0]
    registry = ResultRegistry(
        clock=lambda: now[0],
        pending_timeout_sec=1,
        receipt_observer=store.record_receipt,
    )
    registry.create("task-a", event_id="event-a")
    registry.begin("task-a", event_id="event-a")
    now[0] = 2.0
    registry.lookup("task-a")
    assert not registry.fulfill("task-a", receipt())
    assert store.get(goal["goal_id"])["status"] == "interrupted"
    assert (
        store.get(goal["goal_id"])["processing_receipt"]["terminal_state"]
        == "outcome_unknown"
    )
