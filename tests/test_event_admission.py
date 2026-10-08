"""Admission closes before resources do, and HTTP never reports rejected work."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api_routes import routes_chat, routes_github, routes_proactive
from event.event_bus import EventBus
from event.event_schema import Event
from runtime.result_registry import ResultRegistry
from runtime.websocket import WebSocketManager


def _event() -> Event:
    return Event(type="user_message", source="test", payload={"text": "test"})


def test_close_preserves_pending_work_and_never_persists_rejected_events():
    writes = []
    bus = EventBus(s5_store=SimpleNamespace(store=SimpleNamespace(execute=lambda *args: writes.append(args))))
    accepted = _event()
    assert bus.publish(accepted)
    bus.close()
    bus.close()
    assert bus.accepting is False
    assert bus.publish(_event()) is False
    assert len(writes) == 1
    assert bus.consume(timeout=0) == accepted
    bus.task_done()
    assert bus.drain() == []
    stats = bus.stats()
    assert stats["closed"] is True and stats["accepting"] is False
    assert stats["rejected_closed"] == 1
    assert stats["published"] == stats["persisted"] == 1
    assert stats["dropped"] == 0  # Closure is distinct from capacity eviction.


def test_close_waits_for_in_progress_persistence_before_returning():
    entered_write = threading.Event()
    release_write = threading.Event()
    entered_close = threading.Event()
    returned_close = threading.Event()
    writes = []

    def write(*args):
        entered_write.set()
        assert release_write.wait(2), "test failed to release the persistence barrier"
        assert not returned_close.is_set()
        writes.append(args)

    bus = EventBus(s5_store=SimpleNamespace(store=SimpleNamespace(execute=write)))

    def close():
        entered_close.set()
        bus.close()
        returned_close.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        publishing = pool.submit(bus.publish, _event())
        try:
            assert entered_write.wait(1)
            closing = pool.submit(close)
            assert entered_close.wait(1)
            assert not returned_close.wait(0.03)
        finally:
            release_write.set()
        assert publishing.result(timeout=1) is True
        closing.result(timeout=1)
    assert returned_close.is_set()
    assert bus.publish(_event()) is False
    assert len(writes) == 1


def _app(bus, *, unavailable=False):
    app = FastAPI()
    container = SimpleNamespace(
        event_bus=bus,
        result_registry=ResultRegistry(),
        ws_manager=WebSocketManager(),
        system_state={},
    )
    if unavailable:
        container.runtime = SimpleNamespace(controller=SimpleNamespace(accepting=False))
    app.state.container = container
    app.include_router(routes_chat.router)
    app.include_router(routes_github.router)
    app.include_router(routes_proactive.router)
    return app


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_chat_rejects_stopped_or_failed_runtime_without_leaking_waiters(path, unavailable, monkeypatch):
    bus = EventBus()
    if not unavailable:
        bus.close()
    app = _app(bus, unavailable=unavailable)
    container = app.state.container
    container.result_registry.create("unrelated")

    def must_not_wait(*args, **kwargs):
        pytest.fail("Rejected requests must not wait for execution")

    monkeypatch.setattr(container.result_registry, "wait", must_not_wait)
    with TestClient(app) as client:
        response = client.post(path, json={"text": "cannot run"})
    assert response.status_code == 503
    assert response.json()["accepted"] is False
    assert response.json()["error"] == ("runtime_unavailable" if unavailable else "runtime_stopping")
    assert container.result_registry.size() == 1
    assert not container.ws_manager._callbacks.get("chat_token")
    assert not container.ws_manager._callbacks.get("chat_reply")
    assert bus.size() == 0


@pytest.mark.parametrize("reason", ["runtime_stopping", "event_queue_full", "runtime_unavailable"])
def test_maintenance_does_not_report_rejected_event_as_queued(reason):
    bus = EventBus(max_queue_size=1, overflow_policy="drop_newest")
    if reason == "runtime_stopping":
        bus.close()
    elif reason == "event_queue_full":
        bus.publish(_event())
    app = _app(bus, unavailable=reason == "runtime_unavailable")
    with TestClient(app) as client:
        response = client.post("/api/debug/maintenance/trigger")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["error"] == reason


def _webhook(client, secret, delivery_id="retryable-delivery"):
    body = json.dumps({"repository": {"full_name": "test/eva"}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/github", content=body, headers={
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": "sha256=" + signature,
        "Content-Type": "application/json",
    })


def test_webhook_rejected_for_capacity_can_retry_same_delivery(monkeypatch):
    secret = "local-test-secret"
    monkeypatch.setattr(routes_github.settings, "github_webhook_secret", secret)
    bus = EventBus(max_queue_size=1, overflow_policy="drop_newest")
    bus.publish(_event())
    app = _app(bus)
    with TestClient(app) as client:
        rejected = _webhook(client, secret)
        assert rejected.status_code == 503
        assert rejected.json()["error"] == "event_queue_full"
        bus.drain()
        accepted = _webhook(client, secret)
        duplicate = _webhook(client, secret)
    assert accepted.json()["status"] == "ok"
    assert duplicate.json()["status"] == "duplicate"
    assert bus.size() == 1


@pytest.mark.parametrize("unavailable", [False, True])
def test_webhook_rejected_by_runtime_is_not_deduplicated(monkeypatch, unavailable):
    secret = "local-test-secret"
    monkeypatch.setattr(routes_github.settings, "github_webhook_secret", secret)
    bus = EventBus()
    if not unavailable:
        bus.close()
    app = _app(bus, unavailable=unavailable)
    with TestClient(app) as client:
        rejected = _webhook(client, secret)
    assert rejected.status_code == 503
    assert rejected.json()["error"] == ("runtime_unavailable" if unavailable else "runtime_stopping")
    assert not getattr(app.state, "_github_dedup_set", set())
    assert bus.size() == 0
