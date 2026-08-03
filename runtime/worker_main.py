"""Worker process entry-point for isolated agent execution.

This module is the ``target`` of ``multiprocessing.Process`` spawned by
``ProcessAgentWorkerBackend``.  It:

1. Reads ``WorkerRequest`` JSON from stdin.
2. Reconstructs the agent from the registry.
3. Executes the task.
4. Writes ``WorkerResponse`` JSON to stdout.
5. Sends ``WorkerProgress`` (and heartbeats) on a dedicated channel.

The worker never imports FastAPI, ``app.config``, or any live EVA Core
objects — it is an isolated process that receives only serialized data.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

# ── logging ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[worker] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,  # stderr is not part of the IPC protocol
)
logger = logging.getLogger("eva.worker_main")


# ── IPC helpers ─────────────────────────────────────────────────

def _send(response: dict[str, Any]) -> None:
    """Write a single JSON line to stdout and flush.

    Stdout is the IPC channel — every line is one message.
    """
    sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _recv(timeout: float = 30.0) -> dict[str, Any] | None:
    """Read a single JSON line from stdin with a timeout.

    Returns None on timeout or EOF.
    """
    import select

    if sys.platform == "win32":
        # Windows: select doesn't work on stdin — use a polling loop
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = sys.stdin.readline()
            if line:
                try:
                    return json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    logger.error("failed to parse stdin line: %s", exc)
                    continue
            time.sleep(0.05)
        return None
    else:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line.strip())
        except json.JSONDecodeError as exc:
            logger.error("failed to parse stdin line: %s", exc)
            return None


# ── Agent factory (no FastAPI / app imports) ──────────────────

def _build_agent(agent_name: str) -> Any:
    """Construct the requested agent inside the worker process.

    Only built-in agent types are supported. LLM adapters and tool
    registries are created fresh — no live objects cross the IPC boundary.
    """
    from agents.chat_agent import ChatAgent
    from agents.coding_agent import CodingAgent
    from agents.docs_agent import DocsAgent
    from agents.search_agent import SearchAgent

    registry: dict[str, type] = {
        "chat_agent": ChatAgent,
        "coding_agent": CodingAgent,
        "docs_agent": DocsAgent,
        "search_agent": SearchAgent,
    }

    agent_cls = registry.get(agent_name)
    if agent_cls is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    # ChatAgent needs a tool_registry; others are standalone
    if agent_cls is ChatAgent:
        from core.tool_registry import ToolRegistry, create_builtin_tools
        # In the worker, we always pass tool_registry=None for now.
        # Capability grants will control tool access in WRK-04.
        return ChatAgent(tool_registry=None)

    # SearchAgent and CodingAgent take base_dir
    if agent_cls in (SearchAgent, CodingAgent):
        base_dir = os.getcwd()
        return agent_cls(base_dir=base_dir)  # type: ignore[call-arg]

    return agent_cls()


# ── main loop ──────────────────────────────────────────────────

def run_worker() -> None:
    """Main entry point for a worker process.

    Protocol (line-delimited JSON over stdin/stdout):

    1. Worker sends ``{"type": "ready", "worker_id": ..., "pid": ...}``
    2. Core sends ``{"type": "task", ...}`` (a WorkerRequest)
    3. Worker executes, optionally sending progress
    4. Worker sends ``{"type": "response", ...}`` (a WorkerResponse)
    5. Repeat from step 2 until ``{"type": "stop"}``
    """
    worker_id = f"worker-{os.getpid()}-{uuid4().hex[:6]}"
    start_time = time.time()

    logger.info("worker starting worker_id=%s pid=%d", worker_id, os.getpid())

    # ── signal handling ────────────────────────────────────
    shutdown_requested = False

    def _on_term(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        logger.info("worker received signal %d, draining", signum)
        shutdown_requested = True

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_term)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_term)

    # ── announce readiness ─────────────────────────────────
    _send({
        "type": "ready",
        "worker_id": worker_id,
        "pid": os.getpid(),
        "ts": datetime.now(timezone.utc).isoformat(),
    })

    # ── task loop ──────────────────────────────────────────
    while not shutdown_requested:
        msg = _recv(timeout=1.0)  # 1s poll so we can check shutdown
        if msg is None:
            # Send heartbeat
            _send({
                "type": "heartbeat",
                "worker_id": worker_id,
                "status": "idle",
                "active_tasks": 0,
                "pid": os.getpid(),
                "uptime_sec": round(time.time() - start_time, 1),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            continue

        msg_type = msg.get("type", "")

        if msg_type == "stop":
            logger.info("worker received stop command")
            _send({"type": "stopped", "worker_id": worker_id})
            break

        if msg_type == "ping":
            _send({
                "type": "pong",
                "worker_id": worker_id,
                "pid": os.getpid(),
                "uptime_sec": round(time.time() - start_time, 1),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            continue

        if msg_type == "task":
            _handle_task(msg, worker_id, start_time)
            continue

        logger.warning("unknown message type: %s", msg_type)

    logger.info("worker exiting worker_id=%s", worker_id)


def _handle_task(msg: dict[str, Any], worker_id: str, start_time: float) -> None:
    """Execute a single task and send the response."""
    from runtime.worker_protocol import (
        TaskOutcome,
        WorkerProgress,
        WorkerRequest,
        WorkerResponse,
        WorkerStatus,
    )

    task_start = time.perf_counter()
    task_id = msg.get("task_id", "unknown")
    trace_id = msg.get("trace_id", "")
    agent_name = msg.get("agent_name", "")

    logger.info("task start task_id=%s agent=%s", task_id, agent_name)

    try:
        req = WorkerRequest.model_validate(msg)
    except Exception as exc:
        logger.error("invalid WorkerRequest: %s", exc)
        resp = WorkerResponse(
            task_id=task_id,
            agent_name=agent_name,
            trace_id=trace_id,
            ok=False,
            outcome=TaskOutcome.PROTOCOL_ERROR,
            error="invalid request",
            error_detail=str(exc)[:500],
            worker_id=worker_id,
            worker_status=WorkerStatus.IDLE,
        )
        _send({"type": "response", **resp.model_dump()})
        return

    # ── build agent ────────────────────────────────────────
    try:
        agent = _build_agent(req.agent_name)
    except Exception as exc:
        logger.error("agent construction failed: %s", exc)
        resp = WorkerResponse(
            task_id=req.task_id,
            agent_name=req.agent_name,
            trace_id=req.trace_id,
            ok=False,
            outcome=TaskOutcome.PROTOCOL_ERROR,
            error="agent construction failed",
            error_detail=str(exc)[:500],
            worker_id=worker_id,
            worker_status=WorkerStatus.IDLE,
        )
        _send({"type": "response", **resp.model_dump()})
        return

    # ── build AgentTask ────────────────────────────────────
    from agents.base_agent import AgentTask

    agent_task = AgentTask(
        kind=req.agent_task_kind,
        payload=req.payload,
    )

    # ── execute ────────────────────────────────────────────
    try:
        if req.stream:
            def _on_token(token: str) -> None:
                wp = WorkerProgress(
                    task_id=req.task_id,
                    trace_id=req.trace_id,
                    token=token,
                    status="streaming",
                )
                _send({"type": "progress", **wp.model_dump()})

            agent_result = agent.run_stream(agent_task, _on_token)
        else:
            agent_result = agent.run(agent_task)

        duration_ms = int((time.perf_counter() - task_start) * 1000)

        resp = WorkerResponse(
            task_id=req.task_id,
            agent_name=req.agent_name,
            trace_id=req.trace_id,
            ok=agent_result.ok,
            outcome=TaskOutcome.SUCCESS,
            content=agent_result.content,
            summary=agent_result.summary,
            meta=agent_result.meta,
            duration_ms=duration_ms,
            worker_id=worker_id,
            worker_status=WorkerStatus.IDLE,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - task_start) * 1000)
        logger.exception("task execution failed task_id=%s", req.task_id)
        resp = WorkerResponse(
            task_id=req.task_id,
            agent_name=req.agent_name,
            trace_id=req.trace_id,
            ok=False,
            outcome=TaskOutcome.WORKER_CRASH,
            content=f"[{req.agent_name}] error: {exc}",
            summary=str(exc)[:120],
            error=str(exc)[:500],
            error_detail=traceback.format_exc()[-1000:],
            duration_ms=duration_ms,
            worker_id=worker_id,
            worker_status=WorkerStatus.IDLE,
        )

    _send({"type": "response", **resp.model_dump()})
    logger.info(
        "task done task_id=%s agent=%s ok=%s duration_ms=%s",
        req.task_id, req.agent_name, resp.ok, resp.duration_ms,
    )


# ── entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    run_worker()
