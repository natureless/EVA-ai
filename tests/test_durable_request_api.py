"""Durable HTTP request claims use the actual shared processor in both modes."""

import importlib
import sqlite3
from threading import Event
import time

import pytest


@pytest.fixture(params=["legacy", "minimal"])
def durable_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_DURABLE_REQUESTS", "true")
    monkeypatch.setenv("EVA_ENABLE_BUSINESS_GOALS", "true")
    monkeypatch.setenv("EVA_ENABLE_PROCESSING_EPISODES", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def test_audit_summary_recovers_terminal_without_claiming_reply_was_saved(
    durable_client, monkeypatch
):
    client = durable_client
    container = client.app.state.container

    def fail(*args, **kwargs):
        raise RuntimeError("full reply write unavailable")

    monkeypatch.setattr(container.integrations.durable_requests, "record_receipt", fail)
    ack = client.post("/api/chat", json={"text": "Hello"}).json()
    canonical = container.runtime.results.wait(ack["task_id"], timeout=5)
    assert canonical["ok"] and canonical["reply"]
    wait_idle(container)
    assert (
        container.integrations.durable_requests._record(ack["task_id"]).status
        == "claimed"
    )
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(container)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        response = client.get(f"/api/chat/result/{ack['task_id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] and body["terminal_state"] == "succeeded"
        assert body["reply_available"] is False and body["reply"] == ""
        assert body["review"]["passed"] is None
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = container


def wait_idle(container):
    deadline = time.monotonic() + 5
    while not container.runtime.processor.is_idle and time.monotonic() < deadline:
        time.sleep(0.01)
    assert container.runtime.processor.is_idle


def test_actual_http_reply_review_mode_and_goal_survive_restart(
    durable_client, monkeypatch
):
    client = durable_client
    old = client.app.state.container
    store = old.integrations.durable_requests
    original = old.runtime.processor._process_one
    checked = []

    def checked_boundary(event, *args, **kwargs):
        record = store._record(event.correlation_id)
        intent, observed, state = store.action(record.action_id)
        assert observed.status == "executing" and state.version >= 1
        assert intent.parameters["task_id"] == event.correlation_id
        assert store.source_event_for_action(intent.action_id).payload == event.payload
        checked.append(event.id)
        return original(event, *args, **kwargs)

    monkeypatch.setattr(old.runtime.processor, "_process_one", checked_boundary)
    response = client.post(
        "/api/chat",
        json={"text": "Hello", "mode": "deep", "source_event_id": "durable-source"},
    )
    assert response.status_code == 200 and response.json()["accepted"]
    admitted = response.json()
    canonical = old.runtime.results.wait(admitted["task_id"], timeout=10)
    assert (
        canonical["ok"]
        and isinstance(canonical["review"], dict)
        and canonical["mode"] == "deep"
    )
    wait_idle(old)
    assert checked == [admitted["event_id"]]
    assert store._record(admitted["task_id"]).status == "terminal"
    state = client.get("/api/runtime").json()
    assert state["requested"]["enable_durable_requests"] is True
    assert state["extensions"]["durable_requests"]["active_handlers"] == 0
    assert state["extensions"]["durable_requests"]["recovery_replays"] is False
    goal = client.post(
        "/api/goals",
        json={
            "task_id": admitted["task_id"],
            "description": "耐久关联",
            "success_conditions": [{"field": "checks.ready", "expected": True}],
        },
    ).json()
    assert goal["status"] == "pending_verification"
    trace_count = old.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(old)
    assert store._closed
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        polled = client.get(f"/api/chat/result/{admitted['task_id']}")
        assert polled.status_code == 200
        body = polled.json()
        for key in (
            "reply",
            "review",
            "mode",
            "mode_info",
            "terminal_state",
            "memory_write_ids",
            "loop_id",
        ):
            assert body[key] == canonical.get(
                key, {} if key == "memory_write_ids" else None
            )
        assert body["reply_available"] is True
        duplicate = client.post(
            "/api/chat",
            json={"text": "Hello", "mode": "deep", "source_event_id": "durable-source"},
        )
        assert (
            duplicate.status_code == 409
            and duplicate.json()["task_id"] == admitted["task_id"]
        )
        changed = client.post(
            "/api/chat", json={"text": "different", "source_event_id": "durable-source"}
        )
        assert changed.status_code == 409
        assert (
            restarted.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
            == trace_count
        )
        restored_goal = client.get(f"/api/goals/{goal['goal_id']}").json()
        # Existing goal policy interrupts unfinished verification on restart.
        assert restored_goal["status"] == "interrupted"
        assert restored_goal["processing_receipt"]["terminal_state"] == "succeeded"
        assert client.get(f"/api/goals/{goal['goal_id']}/episode").status_code == 200
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = old


@pytest.mark.parametrize("route", ["/api/chat/sync", "/api/chat/stream"])
def test_sync_and_sse_use_same_durable_reviewed_receipt(durable_client, route):
    client = durable_client
    response = client.post(route, json={"text": "Hello", "mode": "normal"})
    assert response.status_code == 200
    if route.endswith("stream"):
        assert "event: result" in response.text and "[DONE]" in response.text
        task_id = response.headers["X-EVA-Task-ID"]
    else:
        task_id = response.json()["task_id"]
        assert response.json()["completed"]
    wait_idle(client.app.state.container)
    payload = client.app.state.container.integrations.durable_requests.lookup(task_id)[
        "payload"
    ]
    assert payload["ok"] and isinstance(payload["review"], dict) and payload["reply"]
    assert client.get(f"/api/chat/result/{task_id}").json()["reply"] == payload["reply"]


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
def test_durable_admission_failure_never_enqueues_or_runs(
    durable_client, monkeypatch, route
):
    client = durable_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    original_count = container.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected busy")

    monkeypatch.setattr(ledger, "reserve", fail)
    response = client.post(route, json={"text": "do not execute"})
    assert response.status_code == 503
    assert response.json()["error"] == "durable_request_unavailable"
    assert container.event_bus.size() == 0 and container.runtime.results.size() == 0
    assert (
        container.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
        == original_count
    )


def test_claim_commit_failure_prevents_memory_model_and_tool_effects(durable_client):
    client = durable_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    trace_count = container.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
    ledger._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "cognitive_action_outbox"
        else sqlite3.SQLITE_OK
    )
    try:
        ack = client.post("/api/chat", json={"text": "Hello"}).json()
        result = container.runtime.results.wait(ack["task_id"], timeout=10)
        assert result["ok"] is False and result["error"] == "request_claim_unavailable"
        assert result["execution_state"] == "not_started"
        wait_idle(container)
        assert (
            container.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
            == trace_count
        )
        assert ledger.load_sync(ledger.subject_id).version == 0
        assert (
            container.integrations.processing_episodes.get_by_event(ack["event_id"])
            is None
        )
    finally:
        ledger._conn.set_authorizer(None)


def test_canonical_durable_receipt_recovers_episode_and_goal_after_observer_failures(
    durable_client, monkeypatch
):
    client = durable_client
    container = client.app.state.container
    release = Event()
    original = container.runtime.processor._process_one

    def gated(*args, **kwargs):
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(container.runtime.processor, "_process_one", gated)

    def fail(*args, **kwargs):
        raise RuntimeError("observer failure")

    monkeypatch.setattr(
        container.integrations.processing_episodes, "record_receipt", fail
    )
    monkeypatch.setattr(container.integrations.business_goals, "record_receipt", fail)
    # The durable notification sink captured the original method; make it fail too.
    container.integrations.durable_requests._receipt_sink = fail
    ack = client.post("/api/chat", json={"text": "Hello"}).json()
    try:
        goal = client.post(
            "/api/goals",
            json={
                "task_id": ack["task_id"],
                "description": "恢复终态",
                "success_conditions": [{"field": "checks.ready", "expected": True}],
            },
        ).json()
    finally:
        release.set()
    canonical = container.runtime.results.wait(ack["task_id"], timeout=10)
    assert canonical["ok"]
    wait_idle(container)
    assert (
        container.integrations.durable_requests.stats()["receipt_notifications_pending"]
        == 1
    )
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(container)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        assert (
            client.get(f"/api/chat/result/{ack['task_id']}").json()["reply"]
            == canonical["reply"]
        )
        restored_goal = client.get(f"/api/goals/{goal['goal_id']}").json()
        assert restored_goal["status"] == "interrupted"
        assert restored_goal["processing_receipt"]["terminal_state"] == "succeeded"
        saved = restarted.integrations.processing_episodes.get_by_event(ack["event_id"])
        assert (
            saved.result.status == "succeeded" and saved.metadata["receipt_recovered"]
        )
        assert (
            restarted.integrations.durable_requests.stats()[
                "receipt_notifications_pending"
            ]
            == 0
        )
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = container


def test_default_does_not_create_request_dispatch_store(client):
    container = client.app.state.container
    assert container.integrations.durable_requests is None
    assert (
        container.store.fetchone(
            "SELECT name FROM sqlite_master WHERE name='request_dispatch_receipts'"
        )
        is None
    )
    assert (
        client.get("/api/config/defaults").json()["defaults"]["enable_durable_requests"]
        is False
    )


def test_corrupt_persisted_receipt_poll_fails_closed(durable_client):
    client = durable_client
    container = client.app.state.container
    ack = client.post("/api/chat", json={"text": "Hello"}).json()
    assert container.runtime.results.wait(ack["task_id"], timeout=10)["ok"]
    wait_idle(container)
    container.runtime.results.pop(ack["task_id"])
    ledger = container.integrations.durable_requests
    ledger._conn.execute(
        "UPDATE request_dispatch_receipts SET record_json='{}' WHERE task_id=?",
        (ack["task_id"],),
    )
    ledger._conn.commit()
    response = client.get(f"/api/chat/result/{ack['task_id']}")
    assert (
        response.status_code == 503
        and response.json()["error"] == "durable_request_unavailable"
    )


def test_active_handler_keeps_request_store_open_until_shutdown_retry(
    durable_client, monkeypatch
):
    client = durable_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    entered, release = Event(), Event()
    original = container.runtime.processor._process_one

    def gated(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        assert not ledger._closed
        return original(*args, **kwargs)

    monkeypatch.setattr(container.runtime.processor, "_process_one", gated)
    ack = client.post("/api/chat", json={"text": "Hello"}).json()
    try:
        assert entered.wait(1)
        assert container.runtime.controller.stop(timeout=0.01) is False
        assert not ledger._closed and ledger.stats()["active_handlers"] == 1
    finally:
        release.set()
    assert container.runtime.results.wait(ack["task_id"], timeout=5) is not None
    assert container.runtime.controller.stop(timeout=2)
    assert ledger._closed


def test_prior_receipt_is_reconciled_when_goal_registration_write_was_interrupted(
    durable_client, monkeypatch
):
    client = durable_client
    container = client.app.state.container
    ack = client.post("/api/chat", json={"text": "Hello"}).json()
    assert container.runtime.results.wait(ack["task_id"], timeout=5)["ok"]
    wait_idle(container)
    goals = container.integrations.business_goals
    # Goal insertion commits after its request notification was already acked.
    goal = goals.create(
        task_id=ack["task_id"],
        source_event_id=ack["event_id"],
        description="恢复登记窗口",
        success_conditions=[{"field": "checks.ready", "expected": True}],
    )
    assert goals.processing_receipt_for_task(ack["task_id"], ack["event_id"]) is None
    assert (
        container.integrations.durable_requests.stats()["receipt_notifications_pending"]
        == 0
    )
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(container)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        restored = restarted.integrations.business_goals.get(goal["goal_id"])
        assert restored["processing_receipt"]["terminal_state"] == "succeeded"
        assert restored["status"] == "interrupted"
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = container
