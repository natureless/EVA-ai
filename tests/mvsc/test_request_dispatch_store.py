"""Real SQLite dispatch boundaries, first terminals and bounded restart reads."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from event.event_schema import Event
from packages.kernel.request_dispatch_store import RequestDispatchStore
from runtime.request_persistence import RequestAdmissionError
from runtime.result_registry import ResultRegistry


@pytest.fixture
def clock():
    return SimpleNamespace(
        now=datetime(2026, 10, 1, tzinfo=timezone.utc), monotonic=0.0
    )


@pytest.fixture
def store(tmp_path, clock):
    ledger = RequestDispatchStore(tmp_path / "requests.db", clock=lambda: clock.now)
    yield ledger
    ledger.close()


def event(task="task", identity="event"):
    return Event(
        id=identity,
        type="user_message",
        source="user",
        payload={"text": "你好", "mode": "deep"},
        correlation_id=task,
    )


def admit(store, item=None, ttl=5):
    item = item or event()
    assert store.reserve(item, timeout_sec=10, retention_sec=ttl) == {}
    return item


def receipt(item=None, **fields):
    item = item or event()
    return {
        "task_id": item.correlation_id,
        "event_id": item.id,
        "ok": True,
        "terminal_state": "succeeded",
        "reply": "经过审查的回复",
        "review": {"status": "passed", "passed": True, "fact_verified": False},
        "mode": "deep",
        **fields,
    }


def test_claim_atomically_binds_revision_intent_and_original_source(store):
    item = admit(store)
    assert store.claim(item) == "started"
    record = store._record("task")
    intent, observation, state = store.action(record.action_id)
    assert state.version == 1 and state.tick == 0
    assert intent.parameters["task_id"] == "task"
    assert observation.status == "executing"
    assert observation.started_at == record.started_at
    assert store.source_event_for_action(intent.action_id).payload == item.payload
    assert store.stats()["active_handlers"] == 1
    with pytest.raises(RuntimeError):
        store.close()
    with pytest.raises(RuntimeError):
        store.recover()
    store.record_receipt(receipt(item))
    store.end_processing("task", "event", returned_ok=True)
    assert store.action(intent.action_id)[1].status == "completed"


@pytest.mark.parametrize(
    "table",
    [
        "cognitive_states",
        "cognitive_state_commits",
        "cognitive_action_outbox",
        "request_dispatch_receipts",
    ],
)
def test_claim_sql_failures_roll_back_all_dispatch_state(store, table):
    item = admit(store)
    store._conn.set_authorizer(
        lambda op, target, *args: sqlite3.SQLITE_DENY
        if target == table and op in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}
        else sqlite3.SQLITE_OK
    )
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.claim(item)
    finally:
        store._conn.set_authorizer(None)
    assert store._record("task").status == "pending"
    assert store.load_sync(store.subject_id).version == 0
    assert (
        store._conn.execute("SELECT COUNT(*) FROM cognitive_action_outbox").fetchone()[
            0
        ]
        == 0
    )
    assert store.stats()["active_handlers"] == 0


def test_two_connections_claim_once_without_leases(tmp_path):
    first = RequestDispatchStore(tmp_path / "shared.db")
    second = RequestDispatchStore(tmp_path / "shared.db")
    item = admit(first)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(lambda ledger: ledger.claim(item), (first, second)))
        assert sorted(answers) == ["running", "started"]
        winner = first if answers[0] == "started" else second
        winner.record_receipt(receipt(item))
        winner.end_processing("task", "event", returned_ok=True)
        assert first.load_sync(first.subject_id).version == 1
        assert first.claim(item) == "terminal"
    finally:
        first.close()
        second.close()


def test_claim_rejects_changed_payload_under_identical_ids(store):
    item = admit(store)
    changed = item.model_copy(update={"payload": {"text": "write a file"}})
    assert store.claim(changed) == "event_mismatch"
    assert store.load_sync(store.subject_id).version == 0
    assert store.claim(item) == "started"
    store.end_processing("task", "event", returned_ok=None)


def test_original_utc_deadline_survives_clock_and_restart(store, clock):
    item = admit(store)
    clock.now += timedelta(seconds=11)
    assert store.claim(item) == "terminal"
    assert store.lookup("task")["payload"]["terminal_state"] == "expired"
    assert store.load_sync(store.subject_id).version == 0


def test_terminal_is_immutable_and_poll_retention_does_not_restart(store, clock):
    item = admit(store)
    store.record_receipt(receipt(item))
    first = store.lookup("task")
    store.record_receipt(
        receipt(item, ok=False, terminal_state="outcome_unknown", reply="late")
    )
    assert store.lookup("task") == first
    first["payload"]["reply"] = "mutated copy"
    assert store.lookup("task")["payload"]["reply"] != first["payload"]["reply"]
    clock.now += timedelta(seconds=5)
    assert store.lookup("task")["state"] == "missing"
    assert store.summary_for_task("task")["ok"]
    store.recover()
    assert store._record("task").canonical is None
    assert (
        store.reserve(event("new", "event"), timeout_sec=10, retention_sec=5)["task_id"]
        == "task"
    )


@pytest.mark.parametrize(
    "started,elapsed,expected",
    [(False, 0, "rejected"), (False, 11, "expired"), (True, 0, "outcome_unknown")],
)
def test_recovery_never_replays_unfinished_requests(
    tmp_path, clock, started, elapsed, expected
):
    path = tmp_path / "recovery.db"
    ledger = RequestDispatchStore(path, clock=lambda: clock.now)
    item = admit(ledger)
    if started:
        assert ledger.claim(item) == "started"
        # Test-only emulate abrupt process exit. Real subprocess cases below.
        ledger._conn.close()
        ledger._closed = True
    else:
        ledger.close()
    clock.now += timedelta(seconds=elapsed)
    recovered = RequestDispatchStore(path, clock=lambda: clock.now)
    try:
        assert recovered.recover() == 1
        assert recovered.recover() == 0
        assert recovered.lookup("task")["payload"]["terminal_state"] == expected
        assert recovered.claim(item) == "terminal"
        if started:
            record = recovered._record("task")
            assert (
                recovered.action(record.action_id)[1].observation_kind
                == "recovery_unknown"
            )
    finally:
        recovered.close()


def test_valid_summary_recovers_outcome_without_inventing_reply(tmp_path):
    path = tmp_path / "summary.db"
    ledger = RequestDispatchStore(path)
    item = admit(ledger)
    assert ledger.claim(item) == "started"
    ledger.end_processing("task", "event", returned_ok=True)
    ledger.close()
    recovered = RequestDispatchStore(path)
    try:
        proof = {
            "task_id": "task",
            "event_id": "event",
            "ok": True,
            "terminal_state": "succeeded",
        }
        recovered.recover(receipt_lookup=lambda _: proof)
        payload = recovered.lookup("task")["payload"]
        assert (
            payload["ok"]
            and payload["reply_available"] is False
            and payload["reply"] == ""
        )
        assert payload["review"]["passed"] is None
        assert recovered.claim(item) == "terminal"
    finally:
        recovered.close()


def test_recovery_rejects_forged_summary_and_corrupt_record(store):
    item = admit(store)
    with pytest.raises(ValueError):
        store.recover(receipt_lookup=lambda _: {**receipt(item), "event_id": "wrong"})
    assert store._record("task").status == "pending"
    raw = store._record("task").model_dump(mode="json")
    raw["schema_version"] = True
    store._conn.execute(
        "UPDATE request_dispatch_receipts SET record_json=?", (json.dumps(raw),)
    )
    store._conn.commit()
    with pytest.raises(ValueError):
        store.lookup("task")


def test_goal_notification_failure_retries_only_receipt_after_restart(tmp_path):
    path = tmp_path / "notifications.db"
    ledger = RequestDispatchStore(path)
    item = admit(ledger)
    ledger.record_receipt(receipt(item))

    def fail(_):
        raise RuntimeError("sink unavailable")

    with pytest.raises(RuntimeError):
        ledger.attach_receipt_sink(fail)
    assert ledger.stats()["receipt_notifications_pending"] == 1
    ledger.close()
    restarted = RequestDispatchStore(path)
    delivered = []
    try:
        restarted.attach_receipt_sink(delivered.append)
        assert delivered == [
            {
                "task_id": "task",
                "event_id": "event",
                "ok": True,
                "terminal_state": "succeeded",
            }
        ]
        assert restarted.stats()["receipt_notifications_pending"] == 0
        assert restarted.load_sync(restarted.subject_id).version == 0
    finally:
        restarted.close()


def test_registry_receipt_failure_never_repeats_handler(store, monkeypatch):
    registry = ResultRegistry(persistence=store)
    item = event()
    assert registry.create("task", event_id="event", event=item)
    assert registry.begin("task", event_id="event", event=item) == "started"
    original = store.record_receipt
    monkeypatch.setattr(
        store,
        "record_receipt",
        lambda _: (_ for _ in ()).throw(RuntimeError("failed write")),
    )
    assert registry.fulfill("task", receipt(item))
    assert registry.peek("task")["ok"]
    assert registry.begin("task", event_id="event", event=item) == "terminal"
    monkeypatch.setattr(store, "record_receipt", original)
    registry.end_processing("task", event_id="event", returned_ok=True)
    assert store.lookup("task")["payload"]["reply"] == receipt()["reply"]
    assert store.stats()["active_handlers"] == 0


def test_registry_active_handler_finishes_after_ram_retention_expires(store, clock):
    registry = ResultRegistry(
        persistence=store,
        retention_sec=1,
        pending_timeout_sec=1,
        clock=lambda: clock.monotonic,
    )
    item = event()
    assert registry.create("task", event_id="event", event=item)
    assert registry.begin("task", event_id="event", event=item) == "started"
    clock.monotonic = 2
    assert registry.peek("task")["terminal_state"] == "outcome_unknown"
    clock.monotonic = 3
    registry.cleanup()
    assert "task" not in registry._pending
    registry.end_processing("task", event_id="event", returned_ok=None)
    assert store.stats()["active_handlers"] == 0


def test_durable_capacity_and_admission_failure_leave_no_ram_reservation(
    tmp_path, monkeypatch
):
    ledger = RequestDispatchStore(tmp_path / "capacity.db", capacity=1)
    registry = ResultRegistry(persistence=ledger)
    try:
        item = event()
        assert registry.create("task", event_id="event", event=item)
        with pytest.raises(RequestAdmissionError) as error:
            registry.create("next", event_id="next", event=event("next", "next"))
        assert error.value.reason == "result_capacity_full"
        assert registry.size() == 1
        monkeypatch.setattr(
            ledger,
            "reserve",
            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("busy")),
        )
        with pytest.raises(RequestAdmissionError) as error:
            registry.create("next", event_id="next", event=event("next", "next"))
        assert error.value.reason == "durable_request_unavailable"
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("source_hash", "0" * 64),
        ("retention_sec", float("nan")),
        ("subject_id", "foreign"),
    ],
)
def test_corrupt_admission_fails_closed_without_creating_intent(store, field, value):
    item = admit(store)
    raw = store._record("task").model_dump(mode="json")
    raw[field] = value
    store._conn.execute(
        "UPDATE request_dispatch_receipts SET record_json=?", (json.dumps(raw),)
    )
    store._conn.commit()
    with pytest.raises(ValueError):
        store.claim(item)
    assert store.load_sync(store.subject_id).version == 0


def test_registry_queue_rejection_is_durable_without_claim(store):
    item = event()
    registry = ResultRegistry(persistence=store)
    assert registry.create("task", event_id="event", event=item)
    registry.reject_unpublished("task", "event_queue_full")
    assert registry.size() == 0
    polled = registry.lookup("task")["payload"]
    assert (
        polled["terminal_state"] == "rejected"
        and polled["execution_state"] == "not_started"
    )
    assert store.claim(item) == "terminal"
    assert store.load_sync(store.subject_id).version == 0


def test_reopening_does_not_extend_terminal_reply_retention(tmp_path, clock):
    path = tmp_path / "retention.db"
    first = RequestDispatchStore(path, clock=lambda: clock.now)
    item = admit(first)
    first.record_receipt(receipt(item))
    first.close()
    clock.now += timedelta(seconds=4)
    second = RequestDispatchStore(path, clock=lambda: clock.now)
    try:
        assert second.recover() == 0
        assert second.lookup("task")["expires_in_sec"] == 1
        clock.now += timedelta(seconds=1)
        assert second.lookup("task")["state"] == "missing"
    finally:
        second.close()


def test_durable_reply_size_limit_preserves_claim_without_faking_success(store):
    item = admit(store)
    assert store.claim(item) == "started"
    with pytest.raises(ValueError):
        store.record_receipt(receipt(item, reply="x" * 262_144))
    assert store._record("task").status == "claimed"
    store.end_processing("task", "event", returned_ok=None)
    store.recover()
    assert store.lookup("task")["payload"]["terminal_state"] == "outcome_unknown"


@pytest.mark.parametrize("window", ["pending", "claimed", "effect", "receipt"])
def test_real_process_exit_restores_receipts_without_second_file_effect(
    tmp_path, window
):
    path = tmp_path / "crash.db"
    target = tmp_path / "effects.txt"
    script = """
import os, sys
from event.event_schema import Event
from packages.kernel.request_dispatch_store import RequestDispatchStore
store = RequestDispatchStore(sys.argv[1])
event = Event(id='event', type='user_message', source='user', payload={'text':'write'}, correlation_id='task')
store.reserve(event, timeout_sec=300, retention_sec=60)
window = sys.argv[3]
if window != 'pending':
    store.claim(event)
if window in ('effect','receipt'):
    with open(sys.argv[2], 'a', encoding='utf-8') as output:
        output.write('effect\\n')
if window == 'receipt':
    store.record_receipt({'task_id':'task','event_id':'event','terminal_state':'succeeded','ok':True,'reply':'reviewed reply','review':{'passed':True}})
os._exit(47)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(target), window],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 47, result.stderr.decode()
    restored = RequestDispatchStore(path)
    try:
        while restored.recover():
            pass
        payload = restored.lookup("task")["payload"]
        assert (
            payload["terminal_state"]
            == {
                "pending": "rejected",
                "claimed": "outcome_unknown",
                "effect": "outcome_unknown",
                "receipt": "succeeded",
            }[window]
        )
        assert ResultRegistry(persistence=restored).lookup("task")["payload"] == payload
        assert restored.stats()["active_handlers"] == 0
        assert (
            target.read_text(encoding="utf-8") == "effect\n"
            if target.exists()
            else window in {"pending", "claimed"}
        )
    finally:
        restored.close()
