"""Spawn-compatible worker entry point. Task data uses bounded JSON pipe frames.

Workers have no live Core stores, executors or tool registries. This initial
backend supports chat and document text only; privileged agents fail closed.
Process isolation is a lifecycle boundary, not an OS security sandbox.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from multiprocessing.connection import Connection
from typing import Any

from runtime.worker_protocol import (
    MAX_MESSAGE_BYTES,
    TaskOutcome,
    WorkerRequest,
    WorkerResponse,
    decode_message,
    encode_message,
)

logger = logging.getLogger(__name__)


def execute_request(req: WorkerRequest, worker_id: str, llm_max_retries: int) -> WorkerResponse:
    """Return raw agent output. Core reviews it before any client delivery."""
    from agents.base_agent import AgentTask
    from agents.chat_agent import ChatAgent
    from agents.docs_agent import DocsAgent

    started = time.monotonic()
    identity = dict(
        task_id=req.task_id,
        agent_name=req.agent_name,
        trace_id=req.trace_id,
        correlation_id=req.correlation_id,
        causation_id=req.causation_id,
        loop_id=req.loop_id,
        worker_id=worker_id,
    )
    if req.agent_name in {"search_agent", "coding_agent"}:
        return WorkerResponse(
            **identity,
            ok=False,
            outcome=TaskOutcome.POLICY_DENIED,
            error="worker_capability_unavailable",
            content="进程后端尚未开放文件 Agent；请使用线程后端处理文件任务。",
        )
    if req.agent_name not in {"chat_agent", "docs_agent"}:
        return WorkerResponse(
            **identity,
            ok=False,
            outcome=TaskOutcome.PROTOCOL_ERROR,
            error="unknown_agent",
            content="Unknown worker agent",
        )
    try:
        agent = (
            ChatAgent(tool_registry=None, llm_max_retries=llm_max_retries)
            if req.agent_name == "chat_agent"
            else DocsAgent()
        )
        task = AgentTask(kind=req.agent_task_kind, payload=req.payload)
        # No raw progress is sent across IPC: a rejected draft must not reach SSE/WS.
        result = agent.run_stream(task, lambda _: None) if req.stream else agent.run(task)
        return WorkerResponse(
            **identity,
            ok=result.ok,
            outcome=TaskOutcome.SUCCESS if result.ok else TaskOutcome.EXECUTION_ERROR,
            content=result.content,
            summary=result.summary,
            meta={**result.meta, "worker_tools_available": False},
            error=str(result.meta.get("error", "")),
            duration_ms=max(1, int((time.monotonic() - started) * 1000)),
        )
    except Exception:
        logger.exception("worker agent failed task_id=%s", req.task_id)
        return WorkerResponse(
            **identity,
            ok=False,
            outcome=TaskOutcome.EXECUTION_ERROR,
            error="agent_execution_failed",
            content="Worker agent execution failed",
            duration_ms=max(1, int((time.monotonic() - started) * 1000)),
        )


def run_worker(connection: Connection, worker_id: str, llm_max_retries: int = 2) -> None:
    """One request at a time; a separate heartbeat remains active during LLM waits.

    Pipe EOF exits immediately on Windows as well as POSIX. Core terminates the
    process for cancellation/timeouts; a queued stop drains the current request.
    """
    stopped = threading.Event()
    send_lock = threading.Lock()
    state = {"status": "idle"}
    started = time.monotonic()

    def send(message: dict[str, Any]) -> None:
        data = encode_message(message)
        with send_lock:
            connection.send_bytes(data)

    def heartbeat() -> None:
        while not stopped.wait(0.5):
            try:
                send(
                    {
                        "type": "heartbeat",
                        "worker_id": worker_id,
                        "pid": os.getpid(),
                        "status": state["status"],
                        "uptime_sec": time.monotonic() - started,
                    }
                )
            except (OSError, EOFError):
                return

    pulse = threading.Thread(target=heartbeat, name="eva-worker-heartbeat", daemon=True)
    try:
        send({"type": "ready", "worker_id": worker_id, "pid": os.getpid()})
        pulse.start()
        while True:
            message = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES))
            kind = message.pop("type", None)
            if kind == "stop":
                break
            if kind != "task":
                break  # Protocol violation: Core detects EOF and replaces this worker.
            req = WorkerRequest.model_validate(message)
            state["status"] = "busy"
            response = execute_request(req, worker_id, llm_max_retries)
            try:
                send({"type": "response", **response.model_dump(mode="json")})
            except (TypeError, ValueError):
                # Do not send a partial or unbounded result.
                response = WorkerResponse(
                    task_id=req.task_id,
                    agent_name=req.agent_name,
                    trace_id=req.trace_id,
                    correlation_id=req.correlation_id,
                    causation_id=req.causation_id,
                    loop_id=req.loop_id,
                    worker_id=worker_id,
                    ok=False,
                    outcome=TaskOutcome.RESOURCE_EXHAUSTED,
                    error="invalid_or_oversized_result",
                    content="Worker result exceeds the IPC contract",
                )
                send({"type": "response", **response.model_dump(mode="json")})
            state["status"] = "idle"
    except (EOFError, OSError):
        pass
    except Exception:
        logger.exception("worker protocol failure")
    finally:
        stopped.set()
        connection.close()
        if pulse.is_alive():
            pulse.join(timeout=0.6)
