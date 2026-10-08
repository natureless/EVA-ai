"""Receipt projection survives crashes without replaying the source action."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event as Gate

import pytest

from event.event_schema import Event
from memory.episodes import EpisodeRecord, EpisodeResult, StateReference
from packages.kernel.episode_store import EpisodeStore
from packages.kernel.processing_episodes import ProcessingEpisodes
from packages.minimal_brain.business_goal_store import BusinessGoalStore


def start(journal, *, event_id="event", task_id="task"):
    event = Event(
        id=event_id, type="user_message", source="test", correlation_id=task_id
    )
    assert journal.begin(event, "loop", {})
    return event


def terminal(event, state="succeeded"):
    return {
        "event_id": event.id,
        "task_id": event.correlation_id,
        "terminal_state": state,
        "ok": state == "succeeded",
        "reply": "private model reply",
        "metadata": {"secret": "private"},
    }


def goal(store):
    return store.create(
        task_id="task",
        source_event_id="event",
        description="durable projection",
        success_conditions=[{"field": "checks.ready", "expected": True}],
    )


def test_seal_receipt_and_outbox_roll_back_together(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        event = start(journal)
        journal._conn.set_authorizer(
            lambda op, table, *args: sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_INSERT and table == "episode_receipt_outbox"
            else sqlite3.SQLITE_OK
        )
        with pytest.raises(sqlite3.DatabaseError):
            journal.record_receipt(terminal(event))
        assert journal.get_by_event(event.id) is None
        assert journal.stats()["processing"] == 1
        assert journal.stats()["receipt_notifications_pending"] == 0
        journal._conn.set_authorizer(None)
        journal.record_receipt(terminal(event))
        safe = journal.receipt_for_task("task")
        assert set(safe) == {"task_id", "event_id", "terminal_state", "ok"}
        assert "private" not in json.dumps(safe)
        journal.record_receipt(terminal(event, "failed"))
        assert journal.receipt_for_task("task") == safe
        assert journal.stats()["processing"] == 0
    finally:
        journal.close()


@pytest.mark.parametrize("failure", ["sink", "ack"])
def test_delivery_failure_retries_only_projection_without_changing_goal_twice(
    tmp_path, failure
):
    path = tmp_path / "eva.db"
    goals = BusinessGoalStore(path)
    journal = ProcessingEpisodes(path)
    try:
        g = goal(goals)
        event = start(journal)
        journal.record_receipt(terminal(event))
        if failure == "ack":
            journal._conn.set_authorizer(
                lambda op, table, *args: sqlite3.SQLITE_DENY
                if op == sqlite3.SQLITE_UPDATE and table == "episode_receipt_outbox"
                else sqlite3.SQLITE_OK
            )
            sink = goals.record_receipt
        else:

            def sink(_receipt):
                raise RuntimeError("injected goal persistence failure")

        with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
            journal.deliver_receipts(sink)
        assert journal.stats()["receipt_notifications_pending"] == 1
        assert journal.stats()["receipt_delivery_errors"] == 1
        assert journal.get_by_event("event").result.status == "succeeded"
        journal._conn.set_authorizer(None)
        assert journal.deliver_receipts(goals.record_receipt) == 1
        updated = goals.get(g["goal_id"])
        assert updated["status"] == "pending_verification"
        assert updated["version"] == g["version"] + 1
        assert updated["processing_receipt"]["terminal_state"] == "succeeded"
        assert journal.deliver_receipts(goals.record_receipt) == 0
        assert goals.get(g["goal_id"]) == updated
    finally:
        journal.close()
        goals.close()


@pytest.mark.parametrize("point", ["after_seal", "after_goal_commit"])
def test_actual_process_exit_after_commit_redelivers_only_receipt(tmp_path, point):
    path = tmp_path / "eva.db"
    script = """
import os, sys
from event.event_schema import Event
from packages.kernel.processing_episodes import ProcessingEpisodes
from packages.minimal_brain.business_goal_store import BusinessGoalStore
g = BusinessGoalStore(sys.argv[1])
g.create(task_id="task", source_event_id="event", description="crash",
         success_conditions=[{"field":"checks.ready","expected":True}])
j = ProcessingEpisodes(sys.argv[1])
e = Event(id="event", type="user_message", source="test", correlation_id="task")
j.begin(e, "loop", {})
j.record_receipt({"task_id":"task", "event_id":"event", "terminal_state":"succeeded", "ok":True})
if sys.argv[2] == "after_goal_commit":
    def sink(receipt):
        g.record_receipt(receipt)
        os._exit(41)
    j.deliver_receipts(sink)
os._exit(41)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(path), point],
        capture_output=True,
        timeout=15,
    )
    assert crashed.returncode == 41, crashed.stderr.decode(errors="replace")
    journal = ProcessingEpisodes(path)
    goals = BusinessGoalStore(path)
    try:
        assert journal.recover() == 0
        assert journal.stats()["receipt_notifications_pending"] == 1
        before = journal.get_by_event("event").as_record()
        journal.attach_receipt_sink(goals.record_receipt)
        updated = goals.get_by_task("task")
        assert updated["status"] == "pending_verification" and updated["version"] == 3
        assert updated["processing_receipt"]["ok"] is True
        assert journal.get_by_event("event").as_record() == before
        assert journal.stats()["receipt_notifications_pending"] == 0
        assert not journal.begin(
            Event(
                id="event", type="user_message", source="test", correlation_id="task"
            ),
            "new-loop",
            {},
        )
    finally:
        journal.close()
        goals.close()


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "other-task"},
        {"event_id": "other-event"},
        {"terminal_state": "failed", "ok": False},
        {"ok": 1},
        {"schema_version": 99},
        {"reply": "unapproved payload"},
    ],
)
def test_corrupt_projection_is_never_delivered(tmp_path, change):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        event = start(journal)
        journal.record_receipt(terminal(event))
        safe = journal.receipt_for_task("task")
        if "schema_version" in change:
            journal._conn.execute("UPDATE episode_receipt_outbox SET schema_version=99")
        else:
            journal._conn.execute(
                "UPDATE episode_receipt_outbox SET receipt_json=?",
                (json.dumps({**safe, **change}),),
            )
        journal._conn.commit()
        calls = []
        with pytest.raises(ValueError):
            journal.deliver_receipts(calls.append)
        assert calls == [] and journal.stats()["receipt_notifications_pending"] == 1
        with pytest.raises(ValueError):
            journal.receipt_for_task("task")
    finally:
        journal.close()


def test_delivery_releases_sqlite_before_sink_and_does_not_duplicate_concurrent_calls(
    tmp_path,
):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    entered, release = Gate(), Gate()
    calls = []
    try:
        event = start(journal)
        journal.record_receipt(terminal(event))

        def sink(receipt):
            assert not journal._conn.in_transaction
            assert journal.stats()["receipt_notifications_pending"] == 1
            calls.append(receipt)
            entered.set()
            assert release.wait(5)

        with ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(journal.deliver_receipts, sink)
            try:
                assert entered.wait(2)
                assert (
                    pool.submit(journal.deliver_receipts, sink).result(timeout=2) == 0
                )
            finally:
                release.set()
            assert active.result(timeout=2) == 1
        assert len(calls) == 1
    finally:
        release.set()
        journal.close()


@pytest.mark.parametrize(
    "state,ok",
    [("succeeded", False), ("failed", True), ([], False), ("invented", False)],
)
def test_inconsistent_server_outcome_cannot_seal(tmp_path, state, ok):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        event = start(journal)
        with pytest.raises(ValueError):
            journal.record_receipt(
                {**terminal(event), "terminal_state": state, "ok": ok}
            )
        assert journal.stats()["processing"] == 1
        assert journal.receipt_for_task("task") is None
    finally:
        journal.close()


def test_startup_delivery_drains_multiple_bounded_pages(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        for index in range(35):
            event = start(
                journal, event_id=f"event-{index:02d}", task_id=f"task-{index}"
            )
            journal.record_receipt(terminal(event))
        calls = []
        assert journal.deliver_receipts(calls.append, limit=3) == 3
        assert journal.stats()["receipt_notifications_pending"] == 32
        journal.attach_receipt_sink(calls.append)
        assert len(calls) == 35 and len({item["task_id"] for item in calls}) == 35
        assert journal.stats()["receipt_notifications_pending"] == 0
    finally:
        journal.close()


def test_older_sealed_episode_is_readable_without_inventing_durable_receipt(tmp_path):
    path = tmp_path / "eva.db"
    older = EpisodeStore(path)
    record = EpisodeRecord(
        schema_version=2,
        event_id="event",
        event_type="user_message",
        source="test",
        correlation_id="task",
        state_before=StateReference(version=None),
        result=EpisodeResult(status="succeeded", ok=True),
        metadata={"terminal_state": "succeeded"},
    )
    older.append(record)
    older.close()
    journal = ProcessingEpisodes(path)
    try:
        assert journal.get_by_event("event").as_record() == record.as_record()
        assert journal.recover() == 0
        assert journal.receipt_for_task("task") is None
        assert journal.stats()["receipt_notifications_pending"] == 0
    finally:
        journal.close()


@pytest.mark.parametrize("state", ["succeeded", "failed", "outcome_unknown"])
def test_recovery_uses_existing_server_receipt_and_never_reexecutes(tmp_path, state):
    path = tmp_path / "eva.db"
    goals = BusinessGoalStore(path)
    journal = ProcessingEpisodes(path)
    try:
        g = goal(goals)
        event = start(journal)
        journal.action_started(event.id, "loop", "chat_agent")
        goals.record_receipt(terminal(event, state))
        before = goals.get(g["goal_id"])
        changes = goals._conn.total_changes
        assert journal.recover(receipt_lookup=goals.processing_receipt_for_task) == 1
        assert goals._conn.total_changes == changes
        recovered = journal.get_by_event(event.id)
        assert recovered.metadata["receipt_recovered"] is True
        assert recovered.actions[0].status == "unknown"
        assert recovered.result.status == (
            "unknown" if state == "outcome_unknown" else state
        )
        journal.attach_receipt_sink(goals.record_receipt)
        assert goals.get(g["goal_id"]) == before
        assert journal.stats()["receipt_notifications_pending"] == 0
    finally:
        journal.close()
        goals.close()


@pytest.mark.parametrize("change", [{"event_id": "wrong"}, {"ok": 1}, {"ok": False}])
def test_corrupt_existing_server_receipt_rolls_back_recovery(tmp_path, change):
    path = tmp_path / "eva.db"
    goals = BusinessGoalStore(path)
    journal = ProcessingEpisodes(path)
    try:
        goal(goals)
        event = start(journal)
        goals.record_receipt(terminal(event))
        safe = {
            key: terminal(event)[key]
            for key in ("event_id", "task_id", "terminal_state", "ok")
        }
        goals._conn.execute(
            "UPDATE business_goal_receipts SET receipt_json=?",
            (json.dumps({**safe, **change}),),
        )
        goals._conn.commit()
        with pytest.raises(ValueError):
            journal.recover(receipt_lookup=goals.processing_receipt_for_task)
        assert journal.stats()["processing"] == 1
        assert journal.stats()["receipt_notifications_pending"] == 0
        assert journal.get_by_event(event.id) is None
    finally:
        journal.close()
        goals.close()
