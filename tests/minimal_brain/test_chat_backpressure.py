"""Rejected admission never becomes an accepted or hanging chat request."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_routes.routes_chat import router
from event.event_bus import EventBus
from event.event_schema import Event
from runtime.result_registry import ResultRegistry
from runtime.websocket import WebSocketManager


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
def test_full_bus_rejects_request_and_cleans_only_its_bookkeeping(path, monkeypatch):
    bus = EventBus(max_queue_size=1, overflow_policy="drop_newest")
    earlier = Event(type="user_message", source="test", payload={"text": "already queued"})
    assert bus.publish(earlier)

    registry = ResultRegistry()
    registry.create("unrelated-request")
    ws = WebSocketManager()

    async def existing_subscriber(channel, payload):
        pass

    ws.subscribe("chat_token", existing_subscriber)
    ws.subscribe("chat_reply", existing_subscriber)

    def unexpected_wait(*args, **kwargs):
        pytest.fail("A rejected chat request must not wait for a result")

    monkeypatch.setattr(registry, "wait", unexpected_wait)
    container = SimpleNamespace(
        event_bus=bus,
        result_registry=registry,
        ws_manager=ws,
        system_state={},
    )
    app = FastAPI()
    app.state.container = container
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(path, json={"text": "cannot be admitted", "remember": True})

    assert response.status_code == 503
    assert response.json()["accepted"] is False
    assert "retry" in response.json()["detail"].lower()
    assert response.headers["retry-after"] == "1"
    assert response.headers["content-type"] == "application/json"
    assert registry.size() == 1
    registry.fulfill("unrelated-request", {"ok": True})
    assert registry.peek("unrelated-request") == {"ok": True}
    assert ws._callbacks["chat_token"] == [existing_subscriber]
    assert ws._callbacks["chat_reply"] == [existing_subscriber]
    assert container.system_state == {"pending_events": 1, "pending_results": 1}
    queued = bus.consume(timeout=0)
    assert queued is not None and queued.id == earlier.id
    bus.task_done()
    assert bus.size() == 0
