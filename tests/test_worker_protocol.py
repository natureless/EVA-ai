"""Tests for the worker IPC protocol models.

Covers serialization, version rejection, payload guards, and round-trip
fidelity for WorkerRequest, WorkerResponse, WorkerProgress, WorkerControl,
and WorkerHeartbeat.
"""

from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from runtime.worker_protocol import (
    CURRENT_PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    Capability,
    TaskOutcome,
    WorkerControl,
    WorkerHeartbeat,
    WorkerProgress,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
    is_serializable,
)


# ── WorkerRequest ──────────────────────────────────────────────

class TestWorkerRequest:
    def test_defaults_are_valid(self) -> None:
        req = WorkerRequest(agent_name="chat_agent")
        assert req.protocol_version == CURRENT_PROTOCOL_VERSION
        assert req.agent_name == "chat_agent"
        assert req.task_id != ""
        assert req.trace_id != ""
        assert req.deadline_sec == 120.0
        assert req.stream is False
        assert req.grants == []

    def test_rejects_unsupported_protocol_version(self) -> None:
        with pytest.raises(ValidationError, match="Unsupported protocol version"):
            WorkerRequest(agent_name="test", protocol_version=999)

    def test_rejects_zero_protocol_version(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(agent_name="test", protocol_version=0)

    def test_rejects_empty_agent_name(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(agent_name="")

    def test_rejects_non_serializable_payload(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(
                agent_name="test",
                payload={"callback": lambda x: x},
            )

    def test_rejects_lock_in_payload(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(
                agent_name="test",
                payload={"lock": threading.Lock()},
            )

    def test_rejects_thread_in_payload(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(
                agent_name="test",
                payload={"thread": threading.Thread()},
            )

    def test_serializable_payload_passes(self) -> None:
        req = WorkerRequest(
            agent_name="chat_agent",
            payload={"text": "hello", "nested": {"a": 1, "b": [1, 2, 3]}},
        )
        assert req.payload["text"] == "hello"

    def test_json_round_trip(self) -> None:
        req = WorkerRequest(
            agent_name="search_agent",
            agent_task_kind="search",
            payload={"text": "find *.py", "path": "/tmp"},
            trace_id="trace-001",
            correlation_id="corr-001",
            causation_id="cause-001",
            loop_id="loop-001",
            deadline_sec=30.0,
            grants=[
                {
                    "capability": "file_read",
                    "scope": ["/tmp", "/home/user/projects"],
                    "expires_at": 0.0,
                }
            ],
            stream=True,
        )

        data = req.model_dump_json()
        reloaded = WorkerRequest.model_validate_json(data)

        assert reloaded.agent_name == req.agent_name
        assert reloaded.payload == req.payload
        assert reloaded.trace_id == "trace-001"
        assert reloaded.correlation_id == "corr-001"
        assert reloaded.stream is True
        assert reloaded.deadline_sec == 30.0
        assert len(reloaded.grants) == 1

    def test_to_capability_grants(self) -> None:
        req = WorkerRequest(
            agent_name="coding_agent",
            grants=[
                {"capability": "file_read", "scope": ["/src"]},
                {"capability": "code_exec", "scope": [], "expires_at": 999.0},
            ],
        )
        grants = req.to_capability_grants()
        assert len(grants) == 2
        assert grants[0].capability == Capability.FILE_READ
        assert grants[0].scope == ["/src"]
        assert grants[1].capability == Capability.CODE_EXEC

    def test_agent_task_kind_defaults_to_chat(self) -> None:
        req = WorkerRequest(agent_name="chat_agent")
        assert req.agent_task_kind == "chat"

    def test_deadline_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRequest(agent_name="test", deadline_sec=0)

        with pytest.raises(ValidationError):
            WorkerRequest(agent_name="test", deadline_sec=-5)


# ── WorkerResponse ─────────────────────────────────────────────

class TestWorkerResponse:
    def test_success_response(self) -> None:
        resp = WorkerResponse(
            task_id="task-1",
            agent_name="chat_agent",
            ok=True,
            content="Hello, world!",
            summary="Hello",
            duration_ms=150,
            outcome=TaskOutcome.SUCCESS,
        )
        assert resp.ok is True
        assert resp.content == "Hello, world!"

    def test_failure_response(self) -> None:
        resp = WorkerResponse(
            task_id="task-2",
            agent_name="search_agent",
            ok=False,
            outcome=TaskOutcome.TIMEOUT,
            error="timed out",
            error_detail="exceeded 120s deadline",
            duration_ms=120_000,
        )
        assert resp.ok is False
        assert resp.outcome == TaskOutcome.TIMEOUT

    def test_to_agent_result(self) -> None:
        resp = WorkerResponse(
            task_id="task-3",
            agent_name="chat_agent",
            ok=True,
            content="result content",
            summary="result summary",
            meta={"llm_provider": "mock"},
            worker_id="worker-001",
            trace_id="trace-xyz",
        )
        agent_result = resp.to_agent_result()
        assert agent_result.ok is True
        assert agent_result.agent == "chat_agent"
        assert agent_result.content == "result content"
        assert agent_result.meta["worker_id"] == "worker-001"
        assert agent_result.meta["trace_id"] == "trace-xyz"

    def test_json_round_trip(self) -> None:
        resp = WorkerResponse(
            task_id="task-4",
            agent_name="docs_agent",
            ok=True,
            outcome=TaskOutcome.SUCCESS,
            content="summary of doc...",
            summary="summary",
            meta={"source": "readme.md"},
            duration_ms=340,
            worker_id="w1",
            worker_status=WorkerStatus.IDLE,
        )
        data = resp.model_dump_json()
        reloaded = WorkerResponse.model_validate_json(data)
        assert reloaded.task_id == resp.task_id
        assert reloaded.outcome == TaskOutcome.SUCCESS
        assert reloaded.meta == resp.meta
        assert reloaded.worker_status == WorkerStatus.IDLE

    def test_rejects_non_serializable_meta(self) -> None:
        with pytest.raises(ValidationError):
            WorkerResponse(
                task_id="t1",
                ok=True,
                meta={"fn": lambda: None},
            )


# ── WorkerProgress ─────────────────────────────────────────────

class TestWorkerProgress:
    def test_token_progress(self) -> None:
        wp = WorkerProgress(task_id="task-1", token="Hello")
        assert wp.token == "Hello"
        assert wp.worker_ts != ""

    def test_structured_progress(self) -> None:
        wp = WorkerProgress(
            task_id="task-1",
            status="tool_start",
            detail={"tool": "read_file", "path": "/tmp/test.py"},
        )
        assert wp.status == "tool_start"
        assert wp.detail["tool"] == "read_file"

    def test_rejects_non_serializable_detail(self) -> None:
        with pytest.raises(ValidationError):
            WorkerProgress(
                task_id="task-1",
                detail={"callback": lambda x: x},
            )

    def test_json_round_trip(self) -> None:
        wp = WorkerProgress(
            task_id="task-5",
            trace_id="trace-5",
            token=" world",
            status="streaming",
        )
        data = wp.model_dump_json()
        reloaded = WorkerProgress.model_validate_json(data)
        assert reloaded.token == " world"
        assert reloaded.status == "streaming"


# ── WorkerControl ──────────────────────────────────────────────

class TestWorkerControl:
    def test_ping_command(self) -> None:
        ctrl = WorkerControl(command="ping", worker_id="w1")
        assert ctrl.command == "ping"

    def test_drain_command(self) -> None:
        ctrl = WorkerControl(
            command="drain",
            worker_id="w1",
            reason="scaling down",
            deadline_sec=30.0,
        )
        assert ctrl.command == "drain"
        assert ctrl.deadline_sec == 30.0

    def test_deadline_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            WorkerControl(command="stop", deadline_sec=0)


# ── WorkerHeartbeat ────────────────────────────────────────────

class TestWorkerHeartbeat:
    def test_default_heartbeat(self) -> None:
        hb = WorkerHeartbeat(worker_id="w1")
        assert hb.status == WorkerStatus.IDLE
        assert hb.active_tasks == 0

    def test_busy_heartbeat(self) -> None:
        hb = WorkerHeartbeat(
            worker_id="w2",
            status=WorkerStatus.BUSY,
            active_tasks=3,
            pid=12345,
            memory_rss_mb=128.5,
            uptime_sec=3600.0,
        )
        data = hb.model_dump_json()
        reloaded = WorkerHeartbeat.model_validate_json(data)
        assert reloaded.active_tasks == 3
        assert reloaded.memory_rss_mb == 128.5
        assert reloaded.pid == 12345


# ── Serialization Guards ───────────────────────────────────────

class TestSerializationGuards:
    def test_primitives_are_serializable(self) -> None:
        assert is_serializable(None) is True
        assert is_serializable(42) is True
        assert is_serializable("hello") is True
        assert is_serializable(True) is True
        assert is_serializable(3.14) is True

    def test_simple_dicts_are_serializable(self) -> None:
        assert is_serializable({"a": 1, "b": [2, 3]}) is True

    def test_nested_structure_is_serializable(self) -> None:
        assert is_serializable({
            "text": "hello",
            "items": [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}],
            "meta": {"count": 10, "tags": ["a", "b", "c"]},
        }) is True

    def test_function_is_not_serializable(self) -> None:
        assert is_serializable(lambda x: x) is False

    def test_lock_is_not_serializable(self) -> None:
        assert is_serializable(threading.Lock()) is False

    def test_thread_is_not_serializable(self) -> None:
        def dummy() -> None:
            pass

        t = threading.Thread(target=dummy)
        assert is_serializable(t) is False

    def test_bytes_are_not_serializable(self) -> None:
        assert is_serializable(b"hello") is False

    def test_bytearray_is_not_serializable(self) -> None:
        assert is_serializable(bytearray(b"hello")) is False

    def test_nested_function_is_caught(self) -> None:
        assert is_serializable({"fn": lambda x: x}) is False

    def test_deeply_nested_lock_is_caught(self) -> None:
        assert is_serializable({"a": {"b": {"c": threading.Lock()}}}) is False


# ── Capability Enum ────────────────────────────────────────────

class TestCapability:
    def test_all_values_are_valid_strings(self) -> None:
        for cap in Capability:
            assert isinstance(cap.value, str)
            assert len(cap.value) > 0

    def test_from_string(self) -> None:
        assert Capability("file_read") == Capability.FILE_READ
        assert Capability("code_exec") == Capability.CODE_EXEC


# ── TaskOutcome Enum ───────────────────────────────────────────

class TestTaskOutcome:
    def test_outcome_values(self) -> None:
        outcomes = {o.value for o in TaskOutcome}
        assert "success" in outcomes
        assert "timeout" in outcomes
        assert "worker_crash" in outcomes
        assert "policy_denied" in outcomes


# ── Protocol Version ───────────────────────────────────────────

class TestProtocolVersion:
    def test_current_version_is_supported(self) -> None:
        assert CURRENT_PROTOCOL_VERSION in SUPPORTED_VERSIONS

    def test_current_version_is_positive(self) -> None:
        assert CURRENT_PROTOCOL_VERSION > 0
