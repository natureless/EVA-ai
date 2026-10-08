"""Versioned serializable IPC contracts for isolated agent workers.

WorkerRequest, WorkerProgress, and WorkerResponse carry task data, agent
identity, trace metadata, deadlines, capability grants, and result payloads
across process boundaries. The protocol is deliberately neutral — it is the
single contract between EVA Core and any worker backend implementation.

Design rules:
- All models are JSON-serializable Pydantic types.
- Unknown schema versions are rejected before dispatch.
- Non-serializable payloads (live objects, callables, locks) are rejected.
- Every message carries correlation and causation IDs for audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# ── Current protocol version ──────────────────────────────────

CURRENT_PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS: frozenset[int] = frozenset({CURRENT_PROTOCOL_VERSION})
MAX_MESSAGE_BYTES = 1024 * 1024


# ── Capability Grants ─────────────────────────────────────────

class Capability(str, Enum):
    """Capabilities that may be granted to a worker for a single task.

    A worker receives capability grants as part of the request. Tools
    outside the granted set must be rejected by the worker runtime.
    """

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    CODE_EXEC = "code_exec"
    NETWORK_FETCH = "network_fetch"
    NETWORK_BROWSE = "network_browse"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"


@dataclass(slots=True)
class CapabilityGrant:
    capability: Capability
    scope: list[str] = field(default_factory=list)
    # When the grant expires (epoch seconds). 0 = never.
    expires_at: float = 0.0


# ── Worker Status ─────────────────────────────────────────────

class WorkerStatus(str, Enum):
    """Lifecycle status reported by the worker."""

    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"
    STOPPED = "stopped"
    CRASHED = "crashed"


class TaskOutcome(str, Enum):
    """Final outcome of a task execution."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"
    WORKER_CRASH = "worker_crash"
    PROTOCOL_ERROR = "protocol_error"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    EXECUTION_ERROR = "execution_error"


# ── Serializable Models ───────────────────────────────────────

class WorkerRequest(BaseModel):
    """Dispatched from EVA Core to an isolated worker for execution.

    All fields must be JSON-serializable. Live objects (callables,
    executors, locks, DB connections) are rejected at dispatch time.
    """

    # ── protocol ──
    protocol_version: int = Field(default=CURRENT_PROTOCOL_VERSION, ge=1)

    # ── identity ──
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: str = Field(min_length=1, max_length=128)
    agent_task_kind: str = Field(default="chat", min_length=1, max_length=128)

    # ── payload ──
    payload: dict[str, Any] = Field(default_factory=dict)

    # ── tracing ──
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = ""
    causation_id: str = ""
    loop_id: str = ""

    # ── deadline ──
    deadline_sec: float = Field(default=120.0, gt=0, allow_inf_nan=False)

    # ── capability grants ──
    grants: list[dict[str, Any]] = Field(default_factory=list)

    # ── streaming ──
    stream: bool = False

    @field_validator("protocol_version")
    @classmethod
    def check_version(cls, v: int) -> int:
        if v not in SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported protocol version {v}. "
                f"Supported: {sorted(SUPPORTED_VERSIONS)}"
            )
        return v

    @field_validator("payload")
    @classmethod
    def check_serializable(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Reject payloads that contain non-serializable objects."""
        try:
            _assert_json_serializable(v, "WorkerRequest.payload")
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return v

    def to_capability_grants(self) -> list[CapabilityGrant]:
        grants: list[CapabilityGrant] = []
        for g in self.grants:
            cap = Capability(g.get("capability", ""))
            grants.append(
                CapabilityGrant(
                    capability=cap,
                    scope=g.get("scope", []),
                    expires_at=g.get("expires_at", 0.0),
                )
            )
        return grants

    @field_validator("grants")
    @classmethod
    def check_grants(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for value in values:
            Capability(value.get("capability", ""))
            scope = value.get("scope", [])
            expiry = value.get("expires_at", 0.0)
            if not isinstance(scope, list) or not all(isinstance(p, str) for p in scope):
                raise ValueError("Grant scope must be a list of strings")
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry < 0:
                raise ValueError("Grant expiry must be finite and nonnegative")
            try:
                _assert_json_serializable(value, "WorkerRequest.grants")
            except TypeError as exc:
                raise ValueError(str(exc)) from exc
        return values


class WorkerProgress(BaseModel):
    """Sent from worker → EVA Core during streaming execution.

    Each progress message carries either a token or a structured
    status update. The final message for a task is always a
    WorkerResponse, not a WorkerProgress.
    """

    task_id: str
    trace_id: str = ""

    # Incremental token for streaming
    token: str = ""

    # Structured progress update
    status: str = ""          # e.g. "tool_start", "tool_result", "tool_error"
    detail: dict[str, Any] = Field(default_factory=dict)

    # Wall-clock timestamp set by the worker
    worker_ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @field_validator("detail")
    @classmethod
    def check_serializable(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            _assert_json_serializable(v, "WorkerProgress.detail")
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return v


class WorkerResponse(BaseModel):
    """Sent from worker → EVA Core after task completion (or failure).

    This is the final message for a task. The worker must not send
    any further progress or response for the same task_id.
    """

    # ── identity ──
    task_id: str
    agent_name: str = ""
    trace_id: str = ""

    # ── outcome ──
    correlation_id: str = ""
    causation_id: str = ""
    loop_id: str = ""
    ok: bool
    outcome: TaskOutcome = TaskOutcome.SUCCESS

    # ── result ──
    content: str = ""
    summary: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    # ── timing (ms) ──
    duration_ms: int = 0

    # ── error detail ──
    error: str = ""
    error_detail: str = ""

    # ── worker identity ──
    worker_id: str = ""
    worker_status: WorkerStatus = WorkerStatus.IDLE

    @field_validator("meta")
    @classmethod
    def check_serializable(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            _assert_json_serializable(v, "WorkerResponse.meta")
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return v

    def to_agent_result(self) -> Any:
        """Convert to AgentResult for consumption by the cognition loop."""
        from agents.base_agent import AgentResult

        return AgentResult(
            ok=self.ok,
            agent=self.agent_name,
            content=self.content,
            summary=self.summary or self.content[:120],
            meta={
                **self.meta,
                "worker_id": self.worker_id,
                "outcome": self.outcome.value,
                "trace_id": self.trace_id,
                "correlation_id": self.correlation_id,
                "causation_id": self.causation_id,
                "loop_id": self.loop_id,
                **({"error": self.error} if self.error else {}),
            },
        )


# ── Worker Control Messages ───────────────────────────────────

class WorkerControl(BaseModel):
    """Control message sent from EVA Core to the worker process.

    These are out-of-band relative to task execution and manage
    worker lifecycle.
    """

    command: str  # "ping", "drain", "stop", "restart"
    worker_id: str = ""
    reason: str = ""
    deadline_sec: float = Field(default=10.0, gt=0)


class WorkerHeartbeat(BaseModel):
    """Periodic heartbeat sent from worker → EVA Core."""

    worker_id: str
    status: WorkerStatus = WorkerStatus.IDLE
    active_tasks: int = 0
    pid: int = 0
    memory_rss_mb: float = 0.0
    uptime_sec: float = 0.0
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Serialization Guards ──────────────────────────────────────

def _assert_json_serializable(obj: Any, path: str, _depth: int = 0) -> None:
    """Reject live objects, cycles, non-string keys and non-finite numbers."""
    if _depth > 64:
        raise TypeError(f"{path}: nesting exceeds 64 levels (or contains a cycle)")
    if obj is None or isinstance(obj, (bool, int, str)):
        return
    if isinstance(obj, float) and math.isfinite(obj):
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: JSON object keys must be strings")
            _assert_json_serializable(value, f"{path}.{key}", _depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            _assert_json_serializable(value, f"{path}[{i}]", _depth + 1)
        return
    raise TypeError(f"{path}: {type(obj).__name__} is not a JSON value")


def is_serializable(obj: Any) -> bool:
    try:
        _assert_json_serializable(obj, "<root>")
        return True
    except TypeError:
        return False


def encode_message(message: dict[str, Any]) -> bytes:
    """Bound every IPC frame; never pickle task data or stringify objects."""
    _assert_json_serializable(message, "message")
    data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message exceeds 1 MiB")
    return data


def decode_message(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message exceeds 1 MiB")
    message = json.loads(data)
    if not isinstance(message, dict):
        raise ValueError("IPC message must be an object")
    _assert_json_serializable(message, "message")
    return message
