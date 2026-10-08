"""Selected consumers share one admission, observation, and shutdown contract."""

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api_routes import routes_chat, routes_health, routes_metrics, routes_runtime
from core.cognition_loop import CognitionLoop
from event.event_bus import EventBus
from event.event_schema import Event
from runtime.controller import RuntimeController, ShutdownStep
from runtime.health import HealthService
from runtime.result_registry import ResultRegistry


class Consumer:
    def __init__(self):
        self.is_running = False
        self.stopped = True
        self.starts = 0
        self.stops = 0
        self.stop_requests = 0

    def start(self):
        self.starts += 1
        self.is_running = True

    def request_stop(self):
        self.stop_requests += 1
        self.is_running = False

    def stop(self, timeout=3):
        self.stops += 1
        return self.stopped

    @property
    def stats(self):
        return {"is_running": self.is_running}


class Processor:
    def __init__(self, bus):
        self.event_bus = bus
        self.stop_requested = False
        self.is_idle = True
        self.rejections = []
        self.failure = None

    def request_stop(self):
        self.stop_requested = True

    def reset_stop(self):
        self.stop_requested = False

    def process_event(self, event, *, cognitive_context=None):
        if self.failure:
            raise self.failure
        return {"ok": True}

    def reject_event(self, event, reason):
        self.rejections.append((event.id, reason))
        return {"ok": False, "error": reason}

    def shutdown_owned_backend(self):
        return True

    @property
    def stats(self):
        return {"agent_worker": {"backend": "test"}}


def _controller(*, consumer=None, producers=(), finalizers=()):
    bus = EventBus()
    worker = SimpleNamespace(is_idle=True, stats={"pending": 0})
    processor = Processor(bus)
    state = {"ready": True}
    controller = RuntimeController(
        mode="test", consumer=consumer or Consumer(), processor=processor,
        event_bus=bus, worker_backend=worker, system_state=state,
        producers=producers, finalizers=finalizers,
    )
    return controller


def test_concurrent_start_stop_run_one_consumer_and_finalize_once():
    finalized = []
    controller = _controller(finalizers=(ShutdownStep("storage", lambda: finalized.append("closed")),))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: controller.start(), range(8)))
    assert controller.consumer.starts == 1
    assert controller.accepting is True
    event = Event(type="user_message", source="test", payload={})
    assert controller.event_bus.publish(event)
    with ThreadPoolExecutor(max_workers=4) as pool:
        stopped = list(pool.map(lambda _: controller.stop(), range(8)))
    assert stopped == [True] * 8
    assert controller.consumer.stops == 1
    assert finalized == ["closed"]
    assert controller.processor.rejections == [(event.id, "runtime_stopped")]
    assert controller.event_bus.publish(event) is False
    assert controller.snapshot()["phase"] == "stopped"
    assert controller.system_state["shutdown_status"] == "complete"
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        controller.start()


@pytest.mark.parametrize("busy_component", ["consumer", "worker"])
def test_active_work_preserves_dependencies_until_retry(busy_component):
    finalized = []
    stopped_producers = []
    controller = _controller(
        producers=(ShutdownStep("scheduler", lambda: stopped_producers.append("stopped")),),
        finalizers=(ShutdownStep("storage", lambda: finalized.append("closed")),),
    )
    controller.start()
    if busy_component == "consumer":
        controller.consumer.stopped = False
    else:
        controller.worker_backend.is_idle = False
    assert controller.stop(timeout=0) is False
    assert not finalized
    assert controller.system_state["shutdown_status"] == "incomplete"
    assert controller.accepting is False
    assert controller.event_bus.accepting is False
    controller.consumer.stopped = True
    controller.worker_backend.is_idle = True
    assert controller.stop(timeout=0) is True
    assert finalized == ["closed"]
    assert stopped_producers == ["stopped"]


def test_producer_refusal_blocks_consumers_and_storage_until_it_stops():
    stopped = False
    finalized = []
    controller = _controller(
        producers=(ShutdownStep("scheduler", lambda: stopped),),
        finalizers=(ShutdownStep("storage", lambda: finalized.append("closed")),),
    )
    controller.start()
    assert controller.stop(timeout=0) is False
    assert controller.consumer.stop_requests == 1
    assert controller.consumer.stops == 0
    assert finalized == []
    assert "scheduler" in controller.snapshot()["shutdown"]["errors"]
    stopped = True
    assert controller.stop(timeout=0) is True
    assert finalized == ["closed"]


def test_failed_snapshot_keeps_storage_open_and_retries_only_unfinished_steps():
    calls = []
    fail_snapshot = True

    def save_snapshot():
        calls.append("snapshot")
        if fail_snapshot:
            raise OSError("disk full")

    controller = _controller(finalizers=(
        ShutdownStep("worker", lambda: calls.append("worker")),
        ShutdownStep("snapshot", save_snapshot),
        ShutdownStep("storage", lambda: calls.append("storage")),
    ))
    controller.start()
    assert controller.stop() is False
    assert calls == ["worker", "snapshot"]
    assert controller.system_state["shutdown_status"] == "incomplete"
    assert controller.snapshot()["shutdown"]["errors"] == {"snapshot": "disk full"}
    fail_snapshot = False
    assert controller.stop() is True
    assert calls == ["worker", "snapshot", "snapshot", "storage"]
    assert controller.snapshot()["shutdown"]["errors"] == {}


def test_failed_real_consumer_is_not_hidden_by_ready_flag_in_health_or_http(monkeypatch):
    failed = threading.Event()
    errors = []

    def capture_thread_error(args):
        errors.append(args.exc_value)
        failed.set()

    monkeypatch.setattr(threading, "excepthook", capture_thread_error)
    bus = EventBus()
    processor = Processor(bus)
    consumer = CognitionLoop(bus, processor=processor, poll_timeout_sec=0.01)
    state = {"ready": True, "loop_ready": True}
    controller = RuntimeController(
        mode="legacy", consumer=consumer, processor=processor,
        event_bus=bus, worker_backend=SimpleNamespace(is_idle=True, stats={"pending": 0}),
        system_state=state,
    )
    container = SimpleNamespace(
        runtime=SimpleNamespace(controller=controller), event_bus=bus,
        system_state=state, result_registry=ResultRegistry(),
        health=HealthService(state, event_bus=bus, loop=controller),
        registry=SimpleNamespace(list_agents=lambda: []),
        tiered_memory=None, scheduler=SimpleNamespace(list_jobs=lambda: []),
        policy_engine=None,
    )
    app = FastAPI()
    app.state.container = container
    app.state.rate_limiter = None
    for router in (routes_chat.router, routes_health.router, routes_metrics.router, routes_runtime.router):
        app.include_router(router)
    controller.start()
    try:
        with TestClient(app) as client:
            assert client.get("/health/ready").json()["status"] == "ready"
            processor.failure = RuntimeError("simulated consumer fault")
            assert bus.publish(Event(type="user_message", source="test", payload={}))
            assert failed.wait(1)
            for thread in consumer._threads:
                thread.join(timeout=1)
            assert state["ready"] is True  # Startup state is intentionally stale.
            health = client.get("/health/ready").json()
            assert health["status"] == "not_ready"
            assert health["components"]["cognition_loop"] is False
            runtime = client.get("/api/runtime").json()
            assert runtime["phase"] == "failed"
            assert runtime["consumer_running"] is False
            assert runtime["accepting_events"] is False
            assert client.get("/metrics").json()["runtime"]["phase"] == "failed"
            rejected = client.post("/api/chat", json={"text": "cannot execute"})
            assert rejected.status_code == 503
            assert rejected.json()["error"] == "runtime_unavailable"
            assert bus.size() == container.result_registry.size() == 0
        assert str(errors[0]) == "simulated consumer fault"
    finally:
        assert controller.stop(timeout=1)
