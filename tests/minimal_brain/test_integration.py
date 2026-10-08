"""Integration checks against the real runtime with isolated temporary storage."""

import time
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError


@pytest.fixture
def brain_client(monkeypatch, request):
    # The shared client fixture assigns every mutable storage/log/profile path
    # under tmp_path. Disable optional providers and connectors before it loads.
    monkeypatch.setenv("EVA_ENABLE_MINIMAL_BRAIN", "true")
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    monkeypatch.setenv("EVA_EMBEDDING_PROVIDER", "none")
    monkeypatch.setenv("EVA_AGENT_WORKER_BACKEND", "thread")
    monkeypatch.setenv("EVA_GITHUB_API_TOKEN", "")
    monkeypatch.setenv("EVA_GITHUB_POLL_REPOS", "")
    monkeypatch.setenv("EVA_STORAGE_BACKEND", "sqlite")
    return request.getfixturevalue("client")


def test_minimal_brain_flag_is_opt_in_and_exclusive(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("EVA_ENABLE_MINIMAL_BRAIN", raising=False)
    monkeypatch.delenv("EVA_ENABLE_MVSC_PIPELINE", raising=False)
    assert Settings().enable_minimal_brain is False
    with pytest.raises(ValidationError, match="cannot be enabled together"):
        Settings(enable_minimal_brain=True, enable_mvsc_pipeline=True)


def test_real_bootstrap_chat_has_one_consumer_and_one_result(brain_client, tmp_path):
    container = brain_client.app.state.container
    kernel = container.integrations.minimal_brain
    assert container.settings.db_path.is_relative_to(tmp_path)
    assert container.loop._threads == []
    assert kernel.is_running
    assert container.event_bus.stats()["overflow_policy"] == "drop_newest"

    response = brain_client.post("/api/chat", json={"text": "Hello EVA"})
    assert response.status_code == 200
    queued = response.json()
    result = container.result_registry.wait(queued["task_id"], timeout=10)
    assert result is not None and result["ok"]
    assert result["event_id"] == queued["event_id"]
    assert result["selected_agent"] == "chat_agent"
    # Plain mock greeting is reviewed but carries no factual claim contract.
    assert result["review"]["schema_version"] == 1
    assert result["review"]["fact_verified"] is False
    assert result["memory_write_ids"].get("s3") is None

    deadline = time.monotonic() + 2
    while kernel.stats["completed"] != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert kernel.stats["completed"] == 1
    assert container.loop.stats["total_processed"] == 1
    traces = container.store.fetchall("SELECT * FROM traces")
    assert len(traces) == 1
    assert container.loop._threads == []


def test_status_exposes_kernel_and_probes_active_service(brain_client):
    ready = brain_client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["components"]["cognition_loop"] is True
    state = brain_client.get("/api/state")
    assert state.status_code == 200
    snapshot = state.json()["minimal_brain"]
    assert snapshot["version"] >= 1
    assert "workspace" in snapshot and "goals" in snapshot
    assert "graph" in snapshot


def test_kernel_state_uses_existing_runtime_snapshot(brain_client):
    container = brain_client.app.state.container
    container.save_runtime_snapshot()
    saved = container.snapshot_store.load_latest()
    assert "world_model" in saved and "self_model" in saved
    assert "minimal_brain" in saved
    assert saved["minimal_brain"]["version"] >= 1
    assert isinstance(saved["minimal_brain"]["goals"], list)


def test_busy_shutdown_keeps_worker_and_storage_open():
    from app.bootstrap import shutdown_system
    from event.event_bus import EventBus
    from runtime.controller import RuntimeController, ShutdownStep

    kernel = MagicMock()
    kernel.is_running = True
    kernel.stats = {"processor_busy": True}
    kernel.stop.return_value = False
    worker = MagicMock()
    worker.is_idle = True
    worker.stats = {"pending": 0}
    store = MagicMock()
    save_snapshot = MagicMock()
    loop = MagicMock()
    processor = MagicMock()
    processor.stats = {"total_processed": 0}
    state = {"ready": True}
    controller = RuntimeController(
        mode="minimal", consumer=kernel, processor=processor,
        event_bus=EventBus(), worker_backend=worker, system_state=state,
        finalizers=(
            ShutdownStep("worker", worker.shutdown),
            ShutdownStep("snapshot", save_snapshot),
            ShutdownStep("storage", store.close),
        ),
    )
    container = SimpleNamespace(
        system_state=state, loop=loop,
        runtime=SimpleNamespace(controller=controller),
    )
    assert shutdown_system(container) is False
    loop.stop.assert_not_called()
    kernel.stop.assert_called_once()
    kernel.request_stop.assert_called_once()
    processor.request_stop.assert_called_once()
    worker.shutdown.assert_not_called()
    store.close.assert_not_called()
    save_snapshot.assert_not_called()
    assert container.system_state["ready"] is False
    assert container.system_state["shutdown_status"] == "incomplete"


def test_cancelled_wait_still_blocks_shutdown_until_real_thread_exits(monkeypatch):
    from agents.base_agent import AgentResult
    from app.bootstrap import shutdown_system
    from event.event_bus import EventBus
    from packages.minimal_brain.integration import create_minimal_brain
    from runtime.agent_worker import ThreadAgentWorkerBackend
    from runtime.controller import RuntimeController, ShutdownStep

    entered = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    receipts = []

    def slow_action(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release slow action")
        return AgentResult(ok=True, agent="chat_agent", content="done"), 1

    worker = ThreadAgentWorkerBackend(
        SimpleNamespace(execute=slow_action), timeout_sec=3,
    )
    shutdown_spy = MagicMock(wraps=worker.shutdown)
    monkeypatch.setattr(worker, "shutdown", shutdown_spy)
    caller = threading.Thread(
        target=lambda: receipts.append(worker.execute("chat_agent", None, stop_event=cancel)),
        daemon=True,
    )
    kernel = None
    controller = None
    try:
        caller.start()
        assert entered.wait(2)
        cancel.set()
        caller.join(timeout=2)
        assert not caller.is_alive()
        assert receipts[0][0].meta["status"] == "cancelled"
        assert worker.stats["pending"] == 1

        processor = SimpleNamespace(
            process_event=MagicMock(return_value={"ok": True}),
            reject_event=MagicMock(), request_stop=cancel.set,
            stats={"total_processed": 0},
        )
        container = SimpleNamespace(
            system_state={"ready": True}, github_poller=None,
            scheduler=MagicMock(), loop=MagicMock(),
            integrations=SimpleNamespace(minimal_brain=None),
            runtime=SimpleNamespace(worker_backend=worker, processor=processor, controller=None),
            store=MagicMock(), save_runtime_snapshot=MagicMock(),
            snapshot_store=SimpleNamespace(load_latest=lambda: {}),
            event_bus=EventBus(),
        )
        kernel = create_minimal_brain(container, SimpleNamespace(
            enable_mvsc_pipeline=False, minimal_brain_queue_capacity=8,
            minimal_brain_poll_sec=0.02,
        ))
        container.integrations.minimal_brain = kernel
        controller = RuntimeController(
            mode="minimal", consumer=kernel, processor=processor,
            event_bus=container.event_bus, worker_backend=worker,
            system_state=container.system_state,
            finalizers=(
                ShutdownStep("worker", worker.shutdown),
                ShutdownStep("snapshot", container.save_runtime_snapshot),
                ShutdownStep("storage", container.store.close),
            ),
        )
        container.runtime.controller = controller
        assert shutdown_system(container) is False
        assert container.system_state["shutdown_status"] == "incomplete"
        container.store.close.assert_not_called()
        shutdown_spy.assert_not_called()

        release.set()
        deadline = time.monotonic() + 2
        while worker.stats["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.stats["pending"] == 0
        assert controller.stop(timeout=0.1) is True
        shutdown_spy.assert_called_once()
        container.save_runtime_snapshot.assert_called_once()
        container.store.close.assert_called_once()
        assert container.system_state["shutdown_status"] == "complete"
    finally:
        release.set()
        cancel.set()
        caller.join(timeout=2)
        if controller is not None:
            controller.stop(timeout=1)
        elif kernel is not None:
            kernel.stop(timeout=1)
        worker.shutdown()
