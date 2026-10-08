"""Deterministic faults exercised across real spawn processes, not mocked futures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock

import pytest

from agents.base_agent import AgentTask
from runtime.process_worker import ProcessAgentWorkerBackend
from runtime.worker_protocol import WorkerRequest, decode_message, encode_message, is_serializable


def controlled_worker(connection, worker_id, retries):
    """Only this test entry point installs faults; production has no test switches."""
    from runtime import worker_main

    original = worker_main.execute_request

    def execute(req, identity, retry_count):
        mode = req.payload.get("fault")
        if req.payload.get("marker"):
            Path(req.payload["marker"]).write_text("running", encoding="utf-8")
        if mode == "hang":
            time.sleep(60)
        if mode == "delay":
            time.sleep(0.25)
        if mode == "crash":
            os._exit(17)
        if mode == "malformed":
            connection.send_bytes(b"not-json")
            time.sleep(60)
        response = original(req, identity, retry_count)
        if mode == "wrong_identity":
            response.task_id = "another-task"
        if mode == "oversized":
            response.content = "X" * (1024 * 1024 + 1)
        if mode == "draft":
            response.content = (
                "```eva_response\n"
                + json.dumps(
                    {
                        "message": "UNVERIFIED_MARKER",
                        "risk_level": "low",
                        "claims": [
                            {
                                "text": "UNVERIFIED_MARKER",
                                "status": "verified_fact",
                                "confidence": 0.9,
                                "evidence_ids": [],
                            }
                        ],
                    }
                )
                + "\n```"
            )
        return response

    worker_main.execute_request = execute
    worker_main.run_worker(connection, worker_id, retries)


def silent_worker(connection, worker_id, retries):
    connection.send_bytes(
        encode_message({"type": "ready", "worker_id": worker_id, "pid": os.getpid()})
    )
    decode_message(connection.recv_bytes())
    time.sleep(60)


def no_ready_worker(connection, worker_id, retries):
    time.sleep(60)


@pytest.fixture
def pool():
    pools = []

    def make(**kwargs):
        backend = ProcessAgentWorkerBackend(max_workers=1, **kwargs)
        backend._worker_target = controlled_worker
        pools.append(backend)
        backend.start()
        return backend

    yield make
    for backend in pools:
        workers = list(backend.active_workers.values())
        backend.shutdown()
        assert all(not worker.process.is_alive() for worker in workers)
        assert all(not t.is_alive() for worker in workers for t in worker.threads)


def task(**payload):
    return WorkerRequest(agent_name="chat_agent", payload={"text": "hello", **payload})


def wait_for_marker(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), "worker did not enter task execution"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("hang", "timeout"),
        ("crash", "worker_crash"),
        ("malformed", "protocol_error"),
        ("wrong_identity", "protocol_error"),
        ("oversized", "resource_exhausted"),
    ],
)
def test_fault_disposes_process_and_next_task_succeeds(pool, fault, expected):
    backend = pool(timeout_sec=1 if fault == "hang" else 10)
    before = next(iter(backend.active_workers.values()))
    result, _ = backend.execute(task(fault=fault))
    assert not result.ok and result.meta["outcome"] == expected
    assert not before.process.is_alive()
    after = next(iter(backend.active_workers.values()))
    assert after.process.pid != before.process.pid and after.process.is_alive()
    assert backend.execute(task())[0].ok


def test_external_kill_during_execution_is_not_retried(pool, tmp_path):
    backend = pool(timeout_sec=20)
    before = next(iter(backend.active_workers.values()))
    marker = tmp_path / "started"
    with ThreadPoolExecutor(1) as caller:
        pending = caller.submit(backend.execute, task(fault="hang", marker=str(marker)))
        wait_for_marker(marker)
        before.process.kill()
        result, _ = pending.result(timeout=5)
    assert not result.ok and result.meta["outcome"] == "worker_crash"
    assert backend.stats["submitted"] == 1
    assert backend.execute(task())[0].ok


def test_idle_crash_is_replaced_before_dispatch(pool):
    backend = pool()
    before = next(iter(backend.active_workers.values()))
    before.process.kill()
    before.process.join(timeout=2)
    assert backend.execute(task())[0].ok
    assert backend.stats["restarted"] == 1


def test_recycle_at_exact_task_limit(pool):
    backend = pool(max_tasks_per_worker=2)
    before = next(iter(backend.active_workers.values()))
    assert backend.execute(task())[0].ok
    assert before.process.is_alive()
    assert backend.execute(task())[0].ok
    assert not before.process.is_alive()
    assert backend.stats["restarted"] == 1


def test_parallel_callers_do_not_mix_results():
    backend = ProcessAgentWorkerBackend(max_workers=2, queue_capacity=6)
    try:
        backend.start()
        with ThreadPoolExecutor(6) as callers:
            pending = [callers.submit(backend.execute, task(text=f"message-{i}")) for i in range(6)]
            for i, future in enumerate(pending):
                result, _ = future.result(timeout=10)
                assert result.ok and f"message-{i}" in result.content
        assert backend.stats["completed"] == 6
    finally:
        workers = list(backend.active_workers.values())
        backend.shutdown()
        assert all(not w.process.is_alive() for w in workers)


def test_idle_heartbeats_are_drained(pool):
    backend = pool()
    worker = next(iter(backend.active_workers.values()))
    first = worker.heartbeat_at
    deadline = time.monotonic() + 3
    while worker.heartbeat_at == first and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker.heartbeat_at > first
    assert worker.incoming.empty() and not worker.broken.is_set()


def test_queue_timeout_does_not_kill_running_worker(pool, tmp_path):
    backend = pool(queue_timeout_sec=0.1, timeout_sec=20)
    marker = tmp_path / "started"
    stop = threading.Event()
    with ThreadPoolExecutor(1) as caller:
        running = caller.submit(
            backend.execute, task(fault="hang", marker=str(marker)), stop_event=stop
        )
        try:
            wait_for_marker(marker)
            before = next(iter(backend.active_workers.values()))
            result, _ = backend.execute(task())
            assert result.meta["error"] == "queue_timeout"
            assert before.process.is_alive()
            assert backend.stats["timed_out"] == 0
            assert backend.stats["queue_timed_out"] == 1
        finally:
            stop.set()
        assert running.result(timeout=5)[0].meta["outcome"] == "cancelled"


def test_queue_is_bounded_and_cancellation_terminates_worker(pool, tmp_path):
    backend = pool(queue_capacity=0, timeout_sec=20)
    marker = tmp_path / "started"
    stop = threading.Event()
    before = next(iter(backend.active_workers.values()))
    with ThreadPoolExecutor(1) as caller:
        running = caller.submit(
            backend.execute, task(fault="hang", marker=str(marker)), stop_event=stop
        )
        try:
            wait_for_marker(marker)
            rejected, _ = backend.execute(task())
            assert rejected.meta["error"] == "queue_full"
        finally:
            stop.set()
        assert running.result(timeout=5)[0].meta["outcome"] == "cancelled"
    assert not before.process.is_alive()
    assert backend.execute(task())[0].ok


def test_cancel_before_start_does_not_spawn():
    backend = ProcessAgentWorkerBackend(max_workers=1)
    stop = threading.Event()
    stop.set()
    try:
        result, _ = backend.execute(task(), stop_event=stop)
        assert result.meta["outcome"] == "cancelled"
        assert backend.active_workers == {}
    finally:
        backend.shutdown()


def test_stream_review_holds_rejected_text(pool):
    backend = pool()
    tokens = []
    result, _ = backend.execute_stream(task(fault="draft"), tokens.append)
    assert not result.ok and result.meta["error"] == "response_review_failed"
    assert "UNVERIFIED_MARKER" not in "".join(tokens)
    assert tokens == [result.content]


def test_trace_roundtrip_and_core_interface(pool):
    backend = pool()
    trace = dict(
        task_id="task-1",
        trace_id="trace-1",
        correlation_id="chat-1",
        causation_id="event-1",
        loop_id="loop-1",
    )
    request = AgentTask("chat", {"text": "hello"}, trace)
    result, _ = backend.execute("chat_agent", request)
    assert result.ok
    assert all(result.meta[k] == v for k, v in trace.items() if k != "task_id")
    tokens = []
    result, _ = backend.execute_stream("chat_agent", request, tokens.append)
    assert tokens == [result.content]


def test_executor_denial_prevents_process_start():
    backend = ProcessAgentWorkerBackend(max_workers=1)
    executor = MagicMock(name="file")
    executor.name = "file"
    executor.validate_token.return_value = MagicMock(allowed=False, reason="expired")
    try:
        result, _ = backend.execute(
            "docs_agent",
            AgentTask("summarize", {"text": "hi"}),
            executor=executor,
            token_manager=object(),
            token_id="expired",
        )
        assert not result.ok and "token denied" in result.content
        executor.audit_log.record.assert_called_once()
        assert backend.active_workers == {}
    finally:
        backend.shutdown()


def test_audit_records_review_failure(pool):
    backend = pool()
    executor = MagicMock()
    executor.name = "file"
    executor.validate_token.return_value = MagicMock(allowed=True)
    executor.check_boundaries.return_value = MagicMock(allowed=True)
    result, _ = backend.execute(task(fault="draft"), executor=executor, token_manager=object())
    assert not result.ok and result.meta["review"]["status"] == "blocked"
    assert executor.audit_log.record.call_args.kwargs["status"] == "error"


@pytest.mark.parametrize("name", ["search_agent", "coding_agent"])
def test_file_agents_fail_closed_even_with_grants(pool, name):
    backend = pool()
    request = WorkerRequest(agent_name=name, grants=[{"capability": "file_read", "scope": ["/"]}])
    result, _ = backend.execute(request)
    assert not result.ok and result.meta["outcome"] == "policy_denied"
    assert backend.stats["tools_available"] is False


def test_missing_heartbeat_is_a_timeout(pool):
    backend = pool(heartbeat_timeout_sec=0.2, timeout_sec=5)
    backend._worker_target = silent_worker
    before = next(iter(backend.active_workers.values()))
    before.process.kill()
    before.process.join(timeout=2)
    result, _ = backend.execute(task())
    assert result.meta["error"] == "heartbeat_timeout"


def test_startup_timeout_leaves_no_processes():
    backend = ProcessAgentWorkerBackend(max_workers=1, startup_timeout_sec=0.2)
    backend._worker_target = no_ready_worker
    try:
        with pytest.raises(RuntimeError, match="startup"):
            backend.start()
        assert not backend.active_workers
        assert backend.stats["closing"]
    finally:
        backend.shutdown()


@pytest.mark.parametrize("fault,expected_ok", [("delay", True), ("hang", False)])
def test_shutdown_drains_then_forces_stop(pool, tmp_path, fault, expected_ok):
    backend = pool(shutdown_grace_sec=0.6, timeout_sec=20)
    workers = list(backend.active_workers.values())
    marker = tmp_path / "started"
    with ThreadPoolExecutor(1) as caller:
        running = caller.submit(backend.execute, task(fault=fault, marker=str(marker)))
        wait_for_marker(marker)
        backend.shutdown()
        result, _ = running.result(timeout=3)
    assert result.ok is expected_ok
    assert all(not w.process.is_alive() for w in workers)
    assert backend.execute(task())[0].meta["outcome"] == "cancelled"


def test_core_helper_queue_is_separate_and_bounded(pool):
    backend = pool(queue_capacity=0)
    stop = threading.Event()
    pending = backend.submit(stop.wait, 3)
    try:
        with pytest.raises(RuntimeError, match="full"):
            backend.submit(lambda: None)
        assert backend.execute(task())[0].ok
    finally:
        stop.set()
        pending.result(timeout=3)


@pytest.mark.parametrize(
    "value", [object(), {1, 2}, {1: "integer key"}, float("nan"), float("inf")]
)
def test_strict_serialization_rejects_values_previously_stringified(value):
    assert not is_serializable(value)


def test_cyclic_payload_rejected():
    value = {}
    value["self"] = value
    assert not is_serializable(value)


def test_mutated_request_and_oversized_input_rejected_before_start():
    backend = ProcessAgentWorkerBackend(max_workers=1)
    try:
        request = task()
        request.payload["object"] = object()
        assert backend.execute(request)[0].meta["outcome"] == "protocol_error"
        assert (
            backend.execute(task(text="X" * (1024 * 1024)))[0].meta["outcome"] == "protocol_error"
        )
        assert not backend.active_workers
    finally:
        backend.shutdown()


@pytest.mark.parametrize(
    "settings",
    [
        {"agent_worker_backend": "invalid"},
        {"agent_worker_queue_capacity": -1},
        {"agent_worker_queue_timeout_sec": float("inf")},
        {"agent_worker_max_tasks": 0},
    ],
)
def test_invalid_backend_settings_rejected(settings):
    from app.config import Settings

    with pytest.raises(ValueError):
        Settings(**settings)


@pytest.fixture
def process_client(monkeypatch, request):
    monkeypatch.setenv("EVA_AGENT_WORKER_BACKEND", "process")
    monkeypatch.setenv("EVA_AGENT_WORKER_COUNT", "1")
    return request.getfixturevalue("client")


def test_process_mode_bootstrap_chat_sse_and_shutdown(process_client):
    client = process_client
    backend = client.app.state.container.runtime.worker_backend
    assert backend.stats["backend"] == "process"
    data = client.post("/api/chat/sync", json={"text": "hello"}).json()
    assert data["completed"] and data["ok"], data.get("error")
    response = client.post("/api/chat/stream", json={"text": "hello again"})
    assert response.status_code == 200 and "[DONE]" in response.text
    config = client.get("/api/config").json()
    assert config["runtime"]["agent_worker"]["backend"] == "process"
    denied = client.post("/api/chat/sync", json={"text": "/search marker"}).json()
    assert denied["completed"] and not denied["ok"]
    assert denied["error"] == "worker_capability_unavailable"
    workers = list(backend.active_workers.values())
    backend.shutdown()
    assert all(not worker.process.is_alive() for worker in workers)
