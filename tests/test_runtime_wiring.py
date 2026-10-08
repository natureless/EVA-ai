"""HTTP requests must reach exactly the selected, observed runtime consumer."""

from unittest.mock import Mock

import pytest


@pytest.fixture(params=["legacy", "minimal", "mvsc", "mvsc_failed"])
def wired_client(request, monkeypatch):
    mode = request.param
    env = {
        "EVA_ENABLE_MINIMAL_BRAIN": str(mode == "minimal"),
        "EVA_ENABLE_MVSC_PIPELINE": str(mode.startswith("mvsc")),
        "EVA_EMBEDDING_PROVIDER": "none", "EVA_STORAGE_BACKEND": "sqlite",
        "EVA_AGENT_WORKER_BACKEND": "thread", "EVA_GITHUB_API_TOKEN": "",
        "EVA_GITHUB_POLL_REPOS": "", "EVA_ENABLE_CODE_TOOL": "false",
        "EVA_ENABLE_NETWORK_TOOLS": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if mode == "mvsc_failed":
        from packages.cognition import adapted_loop
        monkeypatch.setattr(adapted_loop, "create_adapted_loop", Mock(side_effect=RuntimeError("test construction failure")))
    client = request.getfixturevalue("client")
    return mode, client


def test_http_consumer_matches_observation(wired_client, monkeypatch, tmp_path):
    mode, client = wired_client
    container = client.app.state.container
    expected = "minimal" if mode == "minimal" else "legacy"
    observed = client.get("/api/runtime").json()
    assert observed["mode"] == expected and observed["consumer_running"] is True
    consumer = container.runtime.controller.consumer
    assert observed["consumer_type"] == f"{type(consumer).__module__}.{type(consumer).__qualname__}"
    assert observed["processor_type"] == "core.event_processor.EventProcessor"
    assert container.runtime.controller.processor is container.loop.processor
    assert container.runtime.controller.event_bus is container.event_bus
    assert (consumer is container.loop) == (mode != "minimal")
    assert container.loop.is_running == (mode != "minimal")
    status = observed["extensions"]["mvsc"]
    assert status["status"] == {"mvsc": "attached", "mvsc_failed": "failed"}.get(mode, "disabled")
    assert status["requested"] == mode.startswith("mvsc")
    assert status["is_http_consumer"] is False
    assert client.get("/api/state").json()["mvsc"] == status
    assert client.get("/health/ready").json()["mvsc"] == status
    config = client.get("/api/config").json()
    assert config["runtime"]["mode"] == expected
    assert config["mvsc"]["attached"] == (mode == "mvsc")
    if mode == "mvsc":
        assert container.mvsc_event_store._db_path == tmp_path / "data/mvsc_event_store.db"
        mvsc_run = Mock(side_effect=AssertionError("MVSC is not the HTTP consumer"))
        monkeypatch.setattr(container.mvsc_loop, "run_once", mvsc_run)

    calls = Mock(wraps=container.runtime.processor.process_event)
    legacy_calls = Mock(wraps=container.loop.process_event)
    monkeypatch.setattr(container.runtime.processor, "process_event", calls)
    monkeypatch.setattr(container.loop, "process_event", legacy_calls)
    queued = client.post("/api/chat", json={"text": "Hello EVA", "schema_version": "1.0", "source_event_id": "wiring-request"}).json()
    receipt = container.result_registry.wait(queued["task_id"], timeout=10)
    assert receipt and receipt["ok"]
    result = client.get(f"/api/chat/result/{queued['task_id']}").json()
    assert result["completed"] and result["timing"]["admission_to_execution_ms"] >= 0
    assert result["timing"]["waiting_age_ms"] is None
    assert calls.call_count == 1
    assert calls.call_args.args[0].id == queued["event_id"]
    assert legacy_calls.call_count == (0 if mode == "minimal" else 1)
    if mode == "mvsc":
        mvsc_run.assert_not_called()
    assert len(container.store.fetchall("SELECT * FROM traces")) == 1
    from memory.memory_api import MemoryAPI
    captured = MemoryAPI(container.store).get_event(queued["event_id"])
    assert captured.schema_version == "1.0" and captured.source_event_id == "wiring-request"
    assert captured.correlation_id == queued["task_id"]

    # The defaults endpoint must ignore the environment of this selected mode.
    defaults = client.get("/api/config/defaults").json()["defaults"]
    assert defaults["enable_minimal_brain"] is False
    assert defaults["enable_mvsc_pipeline"] is False


def test_mvsc_construction_failure_closes_isolated_store(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from packages.kernel import mvsc_bootstrap
    from packages.cognition import adapted_loop
    from event.event_bus import EventBus
    store = mvsc_bootstrap.EventStore(tmp_path / "mvsc_event_store.db")
    close = Mock(wraps=store.close)
    monkeypatch.setattr(store, "close", close)
    constructor = Mock(return_value=store)
    monkeypatch.setattr(mvsc_bootstrap, "EventStore", constructor)
    monkeypatch.setattr(adapted_loop, "create_adapted_loop", Mock(side_effect=RuntimeError("construction failure")))
    container = SimpleNamespace(event_bus=EventBus(), system_state={})
    with pytest.raises(RuntimeError, match="construction failure"):
        mvsc_bootstrap.integrate_mvsc(container, SimpleNamespace(data_dir=tmp_path))
    constructor.assert_called_once_with(tmp_path / "mvsc_event_store.db")
    close.assert_called_once()


def test_receipt_timings_use_monotonic_admission_and_execution_boundaries():
    from runtime.result_registry import ResultRegistry
    now = [10.0]
    registry = ResultRegistry(clock=lambda: now[0], pending_timeout_sec=2)
    assert registry.create("started")
    now[0] = 10.25
    pending = registry.lookup("started")["timing"]
    assert pending["waiting_age_ms"] == 250
    assert pending["admission_to_execution_ms"] is None
    assert registry.begin("started") == "started"
    now[0] = 10.75
    assert registry.begin("started") == "running"  # Must not reset the timestamp.
    registry.fulfill("started", {"ok": True})
    timing = registry.lookup("started")["timing"]
    assert timing == {"admission_to_execution_ms": 250, "admission_to_terminal_ms": 750, "waiting_age_ms": None}
    registry.create("expired")
    now[0] = 13
    expired = registry.lookup("expired")
    assert expired["state"] == "terminal"
    assert expired["timing"]["admission_to_execution_ms"] is None
    assert expired["timing"]["waiting_age_ms"] is None


def test_mvsc_close_failure_is_retryable_by_lifecycle_owner():
    from app.experimental import stop_mvsc
    store = Mock()
    store.close.side_effect = [OSError("busy"), None]
    components = {"event_store": store}
    assert stop_mvsc(components) is False
    assert stop_mvsc(components) is True
    assert store.close.call_count == 2
