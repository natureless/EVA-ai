"""Unclaimed recovery, bounded queue handoff and unchanged claim authority."""

from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from app.config import Settings
from event.event_bus import EventBus
from event.event_schema import Event
from packages.kernel.request_dispatch_store import RequestDispatchStore
from packages.minimal_brain.kernel import MinimalBrainKernel
from runtime.request_recovery import RequestRecoveryPublisher
from runtime.result_registry import ResultRegistry


def message(index="a", **payload):
    return Event(
        id=f"event-{index}",
        type="user_message",
        source="user",
        correlation_id=f"task-{index}",
        payload={"text": "Hello", "mode": "deep", **payload},
    )


@pytest.fixture
def rig(tmp_path):
    clock = SimpleNamespace(
        now=datetime(2026, 10, 1, tzinfo=timezone.utc), monotonic=100.0
    )
    store = RequestDispatchStore(tmp_path / "recover.db", clock=lambda: clock.now)
    registry = ResultRegistry(persistence=store, clock=lambda: clock.monotonic)
    bus = EventBus(max_queue_size=1)
    publisher = RequestRecoveryPublisher(
        store, registry, bus, accepting=lambda: bus.accepting
    )
    yield store, registry, bus, publisher, clock
    publisher.stop()
    for task in tuple(store._claims):
        store.end_processing(task, store._record(task).event_id, returned_ok=None)
    store.close()


def reserve(store, event, timeout=10, ttl=5):
    assert store.reserve(event, timeout_sec=timeout, retention_sec=ttl) == {}


def test_recovery_preserves_exact_source_remaining_deadline_and_retention(rig):
    store, registry, bus, publisher, clock = rig
    event = message(remember=True)
    reserve(store, event)
    clock.now += timedelta(seconds=4)
    store.prepare_resume()
    assert registry.lookup(event.correlation_id)["state"] == "pending"
    assert publisher.step() == 1
    queued = bus.consume(0)
    bus.task_done()
    assert queued.model_dump() == event.model_dump()
    pending = registry._pending[event.correlation_id]
    assert pending.deadline == clock.monotonic + 6
    assert pending.retention_sec == 5
    assert registry.lookup(event.correlation_id)["timing"]["waiting_age_ms"] == 4000
    assert store.load_sync(store.subject_id).version == 0
    assert (
        registry.begin(event.correlation_id, event_id=event.id, event=queued)
        == "started"
    )
    assert (
        registry.begin(event.correlation_id, event_id=event.id, event=queued)
        == "running"
    )
    assert publisher.step() == 0
    registry.fulfill(
        event.correlation_id,
        {"ok": True, "terminal_state": "succeeded", "reply": "reply"},
    )
    registry.end_processing(event.correlation_id, event_id=event.id, returned_ok=True)
    clock.monotonic += 5
    clock.now += timedelta(seconds=5)
    assert registry.lookup(event.correlation_id)["state"] == "missing"


def test_live_admission_is_never_implicitly_published_by_recovery(rig):
    store, registry, bus, publisher, _ = rig
    old = message("old")
    reserve(store, old)
    store.prepare_resume()
    live = message("live")
    assert registry.create(live.correlation_id, event_id=live.id, event=live)
    assert publisher.step() == 1
    assert bus.drain() == [old]
    assert publisher.step() == 0
    assert registry.lookup(live.correlation_id)["state"] == "pending"


def test_queue_pressure_retries_without_losing_or_claiming_old_requests(rig):
    store, _, bus, publisher, _ = rig
    one, two = message("a"), message("b")
    reserve(store, one)
    reserve(store, two)
    store.prepare_resume()
    filler = Event(type="user_message", source="test")
    assert bus.publish(filler)
    assert publisher.step() == 0
    assert store.stats()["resume_candidates"] == 2
    assert store.load_sync(store.subject_id).version == 0
    assert bus.drain() == [filler]
    publisher.step()  # wrap page cursor
    assert publisher.step() == 1
    assert bus.drain() == [one]
    publisher.step()
    assert publisher.step() == 1
    assert bus.drain() == [two]
    assert store.stats()["resume_candidates"] == 0


def test_deadline_can_expire_while_waiting_for_queue_space(rig):
    store, registry, bus, publisher, clock = rig
    event = message()
    reserve(store, event, timeout=1)
    store.prepare_resume()
    bus.publish(Event(type="user_message", source="test"))
    assert publisher.step() == 0
    clock.now += timedelta(seconds=2)
    clock.monotonic += 2
    publisher.step()
    publisher.step()
    assert (
        registry.lookup(event.correlation_id)["payload"]["terminal_state"] == "expired"
    )
    assert store.load_sync(store.subject_id).version == 0
    assert all(item.id != event.id for item in bus.drain())


def test_durable_poll_deadline_is_enforced_before_cache_hydration(rig):
    store, registry, _, _, clock = rig
    event = message()
    reserve(store, event, timeout=1)
    store.prepare_resume()
    clock.now += timedelta(seconds=2)
    assert (
        registry.lookup(event.correlation_id)["payload"]["terminal_state"] == "expired"
    )
    assert store.load_sync(store.subject_id).version == 0


def test_ack_failure_never_publishes_a_second_copy(rig, monkeypatch):
    store, _, bus, publisher, _ = rig
    event = message()
    reserve(store, event)
    store.prepare_resume()
    original = store.acknowledge_resume

    def failed_ack(_):
        raise RuntimeError("ack failed after queue handoff")

    monkeypatch.setattr(store, "acknowledge_resume", failed_ack)
    with pytest.raises(RuntimeError):
        publisher.step()
    assert bus.size() == 1
    with pytest.raises(RuntimeError):
        publisher.step()
    assert bus.size() == 1
    monkeypatch.setattr(store, "acknowledge_resume", original)
    assert publisher.step() == 0
    assert bus.drain() == [event]
    assert store.stats()["resume_candidates"] == 0


def test_bounded_pages_recover_more_than_one_batch_without_new_actions(rig):
    store, _, bus, publisher, _ = rig
    events = [message(f"{index:03d}") for index in range(35)]
    for event in events:
        reserve(store, event)
    store.prepare_resume(limit=7)
    received = []
    for _ in range(100):
        publisher.step()
        received.extend(bus.drain())
        if store.stats()["resume_candidates"] == 0:
            break
    assert {event.id for event in received} == {event.id for event in events}
    assert len(received) == 35
    assert store.load_sync(store.subject_id).version == 0


def test_publisher_waits_for_runtime_and_stops_before_store_closes(rig):
    store, registry, bus, _, _ = rig
    event = message()
    reserve(store, event)
    store.prepare_resume()
    entered, release = threading.Event(), threading.Event()
    original = store.resume_page

    def blocked(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    store.resume_page = blocked
    publisher = RequestRecoveryPublisher(
        store, registry, bus, accepting=lambda: True, poll_sec=0.01
    )
    publisher.start()
    try:
        assert entered.wait(1)
        assert publisher.stop(timeout=0.01) is False
        assert not store._closed
    finally:
        release.set()
    assert publisher.stop(timeout=1) is True
    assert bus.size() == 0


def test_pending_intent_conflict_cannot_resume(rig):
    from event.codec import from_legacy_event
    from packages.contracts.actions import ActionIntent
    from packages.contracts.state import StateDelta

    store, _, _, _, _ = rig
    event = message()
    reserve(store, event)
    state = store.load_sync(store.subject_id)
    planned = StateDelta(base_version=0).apply(state)
    store.commit_sync(
        0,
        planned,
        [from_legacy_event(event, subject_id=store.subject_id)],
        [
            ActionIntent(
                source_event_id=event.id,
                slot="request_processing",
                tool_id="runtime:process_event",
            )
        ],
    )
    with pytest.raises(ValueError):
        store.prepare_resume()
    assert store._record(event.correlation_id).status == "pending"


def test_setting_requires_durable_admission():
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, resume_durable_requests=True, enable_durable_requests=False
        )


def test_volatile_registration_cannot_bypass_existing_durable_claim(rig):
    store, registry, bus, publisher, _ = rig
    event = message()
    reserve(store, event)
    store.prepare_resume()
    assert registry.create(event.correlation_id, event_id=event.id)
    with pytest.raises(ValueError):
        publisher.step()
    assert bus.size() == 0
    assert (
        registry.begin(event.correlation_id, event_id=event.id, event=event)
        == "durable_registration_conflict"
    )
    assert store.load_sync(store.subject_id).version == 0


def test_tombstone_still_blocks_volatile_claim_after_reply_retention(rig):
    store, registry, _, _, clock = rig
    event = message()
    reserve(store, event, timeout=1, ttl=1)
    clock.now += timedelta(seconds=2)
    store.recover()
    clock.now += timedelta(seconds=2)
    store.recover()
    assert registry.lookup(event.correlation_id)["state"] == "missing"
    assert registry.create(event.correlation_id, event_id=event.id)
    assert (
        registry.begin(event.correlation_id, event_id=event.id, event=event)
        == "durable_registration_conflict"
    )
    assert store.load_sync(store.subject_id).version == 0


@pytest.fixture(params=["legacy", "minimal"])
def recovery_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_DURABLE_REQUESTS", "true")
    monkeypatch.setenv("EVA_RESUME_DURABLE_REQUESTS", "true")
    monkeypatch.setenv("EVA_ENABLE_BUSINESS_GOALS", "true")
    monkeypatch.setenv("EVA_ENABLE_PROCESSING_EPISODES", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def crashed_http_admission(container, tmp_path, window="pending", timeout=300):
    ack_path = tmp_path / "ack.json"
    effect_path = tmp_path / "effect.txt"
    script = """
import json, os, sys
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api_routes.routes_chat import router
from event.event_bus import EventBus
from packages.kernel.request_dispatch_store import RequestDispatchStore
from runtime.result_registry import ResultRegistry
store = RequestDispatchStore(sys.argv[1])
registry = ResultRegistry(persistence=store, pending_timeout_sec=float(sys.argv[5]))
bus = EventBus()
app = FastAPI()
app.state.container = SimpleNamespace(event_bus=bus, result_registry=registry, system_state={})
app.include_router(router)
with TestClient(app) as client:
    ack = client.post('/api/chat', json={'text':'Hello','mode':'deep','remember':True,'source_event_id':'crash-http'}).json()
    assert ack['accepted']
    event = bus.consume(0)
    if sys.argv[4] != 'pending':
        assert registry.begin(event.correlation_id, event_id=event.id, event=event) == 'started'
    if sys.argv[4] in ('effect','receipt'):
        with open(sys.argv[3], 'a', encoding='utf-8') as output:
            output.write('effect\\n')
    if sys.argv[4] == 'receipt':
        registry.fulfill(event.correlation_id, {'ok':True,'terminal_state':'succeeded','reply':'saved reply','mode':'deep'})
    with open(sys.argv[2], 'w', encoding='utf-8') as output:
        json.dump(ack, output)
os._exit(48)
"""
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(container.settings.db_path),
            str(ack_path),
            str(effect_path),
            window,
            str(timeout),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 48, completed.stderr.decode()
    return json.loads(ack_path.read_text(encoding="utf-8")), effect_path


def wait_terminal(container, task_id):
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        view = container.runtime.results.lookup(task_id)
        if view["state"] == "terminal":
            return view["payload"]
        time.sleep(0.01)
    raise AssertionError("no terminal receipt")


def restart(client, *, minimal_snapshot=None):
    bootstrap = importlib.import_module("app.bootstrap")
    old = client.app.state.container
    assert bootstrap.shutdown_system(old)
    if minimal_snapshot is not None:
        saved = old.snapshot_store.load_latest() or {}
        saved["minimal_brain"] = minimal_snapshot
        old.snapshot_store.save_latest(saved)
    fresh = bootstrap.bootstrap_system()
    client.app.state.container = fresh
    return bootstrap, old, fresh


def test_http_crash_before_claim_resumes_same_task_and_pending_goal(
    recovery_client, tmp_path
):
    client = recovery_client
    old = client.app.state.container
    ack, _ = crashed_http_admission(old, tmp_path)
    goal = client.post(
        "/api/goals",
        json={
            "task_id": ack["task_id"],
            "description": "等待请求恢复",
            "success_conditions": [{"field": "checks.ready", "expected": True}],
        },
    ).json()
    assert goal["status"] == "active"
    # Actual snapshot of a queued event, without invoking a processor.
    stored_event = old.integrations.durable_requests._unclaimed_event(
        old.integrations.durable_requests._record(ack["task_id"])
    )
    bus = EventBus()
    probe = MinimalBrainKernel(bus, lambda *_: {}, processor_ready=lambda: False)
    try:
        bus.publish(stored_event)
        probe.step()
        snapshot = probe.snapshot()
    finally:
        probe.stop()
    bootstrap, old, fresh = restart(client, minimal_snapshot=snapshot)
    try:
        receipt = wait_terminal(fresh, ack["task_id"])
        assert (
            receipt["ok"] and receipt["mode"] == "deep" and receipt["memory_write_ids"]
        )
        assert (
            receipt["task_id"] == ack["task_id"]
            and receipt["event_id"] == ack["event_id"]
        )
        assert fresh.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 1
        assert (
            client.get(f"/api/goals/{goal['goal_id']}").json()["status"]
            == "pending_verification"
        )
        if fresh.runtime.controller.mode == "minimal":
            assert fresh.integrations.minimal_brain.stats["resumed_pending"] == 1
        state = client.get("/api/runtime").json()
        assert state["extensions"]["request_recovery"]["attached"]
        duplicate = client.post(
            "/api/chat", json={"text": "Hello", "source_event_id": "crash-http"}
        )
        assert duplicate.status_code == 409
    finally:
        assert bootstrap.shutdown_system(fresh)
        client.app.state.container = old


@pytest.mark.parametrize("window", ["claimed", "effect", "receipt"])
def test_claimed_http_crash_never_enters_processor_again(
    recovery_client, tmp_path, window
):
    client = recovery_client
    ack, effect = crashed_http_admission(client.app.state.container, tmp_path, window)
    bootstrap, old, fresh = restart(client)
    try:
        receipt = wait_terminal(fresh, ack["task_id"])
        assert receipt["terminal_state"] == (
            "succeeded" if window == "receipt" else "outcome_unknown"
        )
        assert fresh.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 0
        assert fresh.integrations.durable_requests.stats()["resume_candidates"] == 0
        if effect.exists():
            assert effect.read_text(encoding="utf-8") == "effect\n"
    finally:
        assert bootstrap.shutdown_system(fresh)
        client.app.state.container = old


def test_recovered_request_rechecks_current_policy(
    recovery_client, tmp_path, monkeypatch
):
    from core.policy_engine import Verdict

    client = recovery_client
    ack, _ = crashed_http_admission(client.app.state.container, tmp_path)
    monkeypatch.setattr(RequestRecoveryPublisher, "start", lambda _: None)
    bootstrap, old, fresh = restart(client)
    try:
        monkeypatch.setattr(
            fresh.runtime.policy,
            "evaluate",
            lambda *a, **k: SimpleNamespace(
                verdict=Verdict.DENY, reason="current policy denies"
            ),
        )
        fresh.runtime.request_recovery.step()
        receipt = wait_terminal(fresh, ack["task_id"])
        assert (
            receipt["terminal_state"] == "rejected"
            and receipt["error"] == "policy_denied"
        )
        assert fresh.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 0
        assert (
            fresh.integrations.durable_requests.load_sync(
                fresh.integrations.durable_requests.subject_id
            ).version
            == 1
        )
    finally:
        assert bootstrap.shutdown_system(fresh)
        client.app.state.container = old


def test_original_deadline_is_not_extended_by_restart_settings(
    recovery_client, tmp_path
):
    import time

    client = recovery_client
    ack, _ = crashed_http_admission(client.app.state.container, tmp_path, timeout=0.1)
    time.sleep(0.15)
    bootstrap, old, fresh = restart(client)
    try:
        assert fresh.runtime.results.pending_timeout_sec > 1
        receipt = wait_terminal(fresh, ack["task_id"])
        assert receipt["terminal_state"] == "expired" and receipt["mode"] == "deep"
        assert receipt["execution_state"] == "not_started"
        assert fresh.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 0
    finally:
        assert bootstrap.shutdown_system(fresh)
        client.app.state.container = old


def test_two_restarts_before_queue_handoff_do_not_discard_pending_request(
    recovery_client, tmp_path, monkeypatch
):
    client = recovery_client
    ack, _ = crashed_http_admission(client.app.state.container, tmp_path)
    original_start = RequestRecoveryPublisher.start
    monkeypatch.setattr(RequestRecoveryPublisher, "start", lambda _: None)
    bootstrap, original, second = restart(client)
    third = None
    try:
        assert second.runtime.results.lookup(ack["task_id"])["state"] == "pending"
        assert second.integrations.durable_requests.stats()["resume_candidates"] == 1
        assert second.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 0
        assert bootstrap.shutdown_system(second)
        monkeypatch.setattr(RequestRecoveryPublisher, "start", original_start)
        third = bootstrap.bootstrap_system()
        client.app.state.container = third
        assert wait_terminal(third, ack["task_id"])["ok"]
        assert third.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"] == 1
    finally:
        if third is not None:
            assert bootstrap.shutdown_system(third)
        else:
            assert bootstrap.shutdown_system(second)
        client.app.state.container = original


def test_completed_minimal_snapshot_cannot_override_unclaimed_proof(rig):
    store, _, _, _, _ = rig
    event = message()
    reserve(store, event)
    store.prepare_resume()
    from packages.minimal_brain.goals import GoalLedger

    goals = GoalLedger()
    goal = goals.accept(event)
    goal = goals.transition(event.id, "running", expected_version=goal.version)
    goals.transition(event.id, "completed", expected_version=goal.version)
    kernel = MinimalBrainKernel(
        EventBus(),
        lambda *_: {},
        initial_snapshot={"schema_version": 1, "goals": goals.snapshot()},
    )
    try:
        with pytest.raises(ValueError):
            kernel.prepare_request_resume(store.unclaimed_event)
        assert kernel.stats["accepted"] == 0
        assert store.load_sync(store.subject_id).version == 0
    finally:
        kernel.stop()


def test_resume_source_change_is_rejected_without_erasing_original_goal(rig):
    store, _, _, _, _ = rig
    event = message()
    reserve(store, event)
    store.prepare_resume()
    from packages.minimal_brain.goals import GoalLedger

    goals = GoalLedger()
    goals.accept(event)
    bus = EventBus()
    kernel = MinimalBrainKernel(
        bus,
        lambda *_: {},
        processor_ready=lambda: False,
        initial_snapshot={"schema_version": 1, "goals": goals.snapshot()},
    )
    try:
        kernel.prepare_request_resume(store.unclaimed_event)
        bus.publish(event.model_copy(update={"payload": {"text": "changed"}}))
        kernel.step()
        assert kernel.stats["accepted"] == 0 and kernel.stats["rejected"] == 1
        assert kernel.snapshot()["goals"][0]["status"] == "interrupted"
        assert store.load_sync(store.subject_id).version == 0
    finally:
        kernel.stop()
