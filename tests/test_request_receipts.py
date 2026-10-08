"""Admission/receipt lifetimes with isolated queues, clocks and HTTP apps."""

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api_routes import routes_chat
from event.event_bus import EventBus
from event.event_schema import Event
from runtime.result_registry import ResultRegistry


@pytest.fixture
def clock():
    return SimpleNamespace(now=0.0)


@pytest.fixture
def registry(clock):
    return ResultRegistry(clock=lambda: clock.now, pending_timeout_sec=10, retention_sec=5)


def app_for(bus, registry):
    app = FastAPI()
    app.state.container = SimpleNamespace(event_bus=bus, result_registry=registry, system_state={})
    app.include_router(routes_chat.router)
    return app


@pytest.mark.parametrize("kind,correlation", [("user_message", None), ("github_push", None), ("maintenance", "request")])
def test_interactive_and_correlated_work_is_never_evicted(kind, correlation):
    bus = EventBus(max_queue_size=1)
    protected = Event(type=kind, source="test", correlation_id=correlation)
    assert bus.publish(protected)
    assert not bus.publish(Event(type="system_tick", source="scheduler"))
    assert bus.consume(0) == protected
    bus.task_done()
    assert bus._queue.unfinished_tasks == 0
    assert bus.stats()["rejected_full"] == 1 and bus.stats()["evicted_background"] == 0


def test_background_replacement_preserves_survivor_fifo_and_task_accounting():
    bus = EventBus(max_queue_size=3)
    first = Event(type="user_message", source="user")
    second = Event(type="user_message", source="user")
    last = Event(type="user_message", source="user")
    for item in (first, Event(type="system_tick", source="scheduler"), second):
        assert bus.publish(item)
    assert bus.publish(last)
    assert bus.drain() == [first, second, last]
    assert bus._queue.unfinished_tasks == 0
    assert bus.stats()["evicted_background"] == 1


def test_concurrent_publishers_never_lose_admitted_interactive_events():
    bus = EventBus(max_queue_size=17)
    def publish(index):
        item = Event(type="user_message", source="test", correlation_id=str(index))
        return item.id, bus.publish(item)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(publish, range(500)))
    accepted = {key for key, admitted in outcomes if admitted}
    assert len(accepted) == 17
    assert {item.id for item in bus.drain()} == accepted
    assert bus._queue.unfinished_tasks == 0


@pytest.mark.parametrize("capacity", [0, -1, True])
def test_queue_requires_real_bound(capacity):
    with pytest.raises(ValueError):
        EventBus(max_queue_size=capacity)


def test_cleanup_never_deletes_waiting_request_or_old_admission_on_completion(registry, clock):
    registry.create("a")
    clock.now = 9
    assert registry.cleanup(ttl_sec=0) == 0
    assert registry.lookup("a")["state"] == "pending"
    assert registry.fulfill("a", {"ok": True})
    clock.now = 13
    assert registry.cleanup() == 0
    assert registry.peek("a") == {"ok": True}
    clock.now = 14
    assert registry.lookup("a")["state"] == "missing"


@pytest.mark.parametrize("started", [False, True])
def test_deadline_creates_terminal_without_overwriting_by_late_completion(registry, clock, started):
    registry.create("a", event_id="event-a")
    if started:
        assert registry.begin("a") == "started"
    clock.now = 11
    receipt = registry.peek("a")
    assert receipt["event_id"] == "event-a"
    assert receipt["error"] == "request_deadline_exceeded"
    assert receipt["terminal_state"] == ("outcome_unknown" if started else "expired")
    assert receipt["execution_state"] == ("may_still_be_running" if started else "not_started")
    assert registry.begin("a") == "terminal"
    assert not registry.fulfill("a", {"ok": True})
    assert registry.wait("a", timeout=0) == receipt
    assert registry.cleanup() == 0
    assert registry.peek("a") == receipt


def test_duplicate_ids_and_parallel_completion_preserve_one_terminal(registry):
    registry.create("a")
    assert registry.begin("a") == "started"
    assert registry.begin("a") == "running"
    assert registry.create("a") is False
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda i: (i, registry.fulfill("a", {"winner": i})), range(32)))
    winners = [index for index, wrote in outcomes if wrote]
    assert len(winners) == 1
    assert registry.peek("a") == {"winner": winners[0]}
    copy = registry.peek("a")
    copy["winner"] = -1
    assert registry.peek("a")["winner"] != -1


def test_execution_claim_cannot_substitute_another_event(registry):
    registry.create("a", event_id="original")
    assert registry.begin("a", event_id="different") == "event_mismatch"
    assert registry.lookup("a")["state"] == "pending"
    assert registry.begin("a", event_id="original") == "started"


def test_capacity_preserves_retained_receipts_and_reopens_after_retention(clock):
    registry = ResultRegistry(max_entries=1, clock=lambda: clock.now, retention_sec=5)
    registry.create("a")
    assert not registry.create("b")
    registry.fulfill("a", {"ok": True})
    assert not registry.create("b")
    clock.now = 6
    assert registry.create("b")
    assert registry.stats()["entries"] == registry.stats()["capacity"] == 1


def test_wait_timeout_is_not_request_expiry_and_receipt_waiter_wakes_on_deadline():
    registry = ResultRegistry(pending_timeout_sec=0.04)
    registry.create("a")
    assert registry.wait("a", timeout=0) is None
    result = registry.wait("a", timeout=1)
    assert result["terminal_state"] == "expired"


def test_wait_rechecks_deadline_after_early_timer_wakeup(clock, monkeypatch):
    registry = ResultRegistry(pending_timeout_sec=1, clock=lambda: clock.now)
    registry.create("a")
    waits = []

    def early_wait(timeout):
        waits.append(timeout)
        clock.now = 0.5 if len(waits) == 1 else 1.0
        return False

    monkeypatch.setattr(registry._pending["a"].event, "wait", early_wait)
    assert registry.wait("a", timeout=2)["terminal_state"] == "expired"
    assert len(waits) == 2


def test_shutdown_rejects_waiting_requests_but_does_not_claim_active_cancellation(registry):
    registry.create("waiting")
    registry.create("active")
    registry.begin("active")
    receipts = registry.reject_waiting()
    assert [r["task_id"] for r in receipts] == ["waiting"]
    assert receipts[0]["execution_state"] == "not_started"
    assert registry.lookup("active")["state"] == "running"
    assert registry.reject_waiting() == []


def test_poll_distinguishes_missing_pending_and_repeatable_terminal(registry, clock):
    with TestClient(app_for(EventBus(), registry)) as client:
        assert client.get("/api/chat/result/no-such-task").status_code == 404
        ack = client.post("/api/chat", json={"text": "hello"}).json()
        url = "/api/chat/result/" + ack["task_id"]
        assert client.get(url).status_code == 202
        clock.now = 11
        first = client.get(url)
        second = client.get(url)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["terminal_state"] == "expired"
        clock.now = 16
        assert client.get(url).status_code == 404


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
def test_registry_capacity_rejection_does_not_enqueue_or_delete_other_waiters(route):
    registry = ResultRegistry(max_entries=1)
    registry.create("keep")
    bus = EventBus()
    with TestClient(app_for(bus, registry)) as client:
        response = client.post(route, json={"text": "reject"})
        assert response.status_code == 503
        assert response.json()["error"] == "result_capacity_full"
    assert bus.size() == 0 and registry.lookup("keep")["state"] == "pending"


def test_sync_transport_timeout_keeps_pending_request_queryable(registry, monkeypatch):
    monkeypatch.setattr(routes_chat.settings, "request_timeout_sec", 0.001)
    with TestClient(app_for(EventBus(), registry)) as client:
        response = client.post("/api/chat/sync", json={"text": "hello"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        assert registry.lookup(task_id)["state"] == "pending"
        registry.fulfill(task_id, {"ok": True, "reply": "completed later"})
        for _ in range(2):
            assert client.get(f"/api/chat/result/{task_id}").json()["reply"] == "completed later"


@pytest.mark.parametrize("route", ["/api/chat/sync", "/api/chat/stream"])
def test_fast_completion_survives_transport_and_remains_pollable(route, registry):
    class InstantBus(EventBus):
        def publish(self, event):
            accepted = super().publish(event)
            if accepted:
                registry.fulfill(event.correlation_id, {"ok": True, "reply": "canonical\nreply", "terminal_state": "succeeded"})
            return accepted
    with TestClient(app_for(InstantBus(), registry)) as client:
        response = client.post(route, json={"text": "hello"})
        assert response.status_code == 200
        if route.endswith("stream"):
            assert "event: result" in response.text and "[DONE]" in response.text
            task_id = response.headers["x-eva-task-id"]
        else:
            task_id = response.json()["task_id"]
        for _ in range(2):
            assert client.get(f"/api/chat/result/{task_id}").json()["reply"] == "canonical\nreply"


def test_sse_without_consumer_or_websocket_ends_with_expiry_receipt():
    registry = ResultRegistry(pending_timeout_sec=0.02)
    with TestClient(app_for(EventBus(), registry)) as client:
        response = client.post("/api/chat/stream", json={"text": "no consumer"})
        assert response.status_code == 200 and "[DONE]" in response.text
        result_text = response.text.split("event: result\ndata: ", 1)[1].split("\n\n", 1)[0]
        receipt = json.loads(result_text)
        assert receipt["ok"] is False and receipt["terminal_state"] == "expired"
