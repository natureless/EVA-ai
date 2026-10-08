"""Chat admission and delivery through retained in-process result receipts.

Async returns a task ID. Sync bounds HTTP waiting. SSE and polling read the
same terminal record; WebSocket chat_reply is an additional delivery channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Any, AsyncGenerator, Literal
from uuid import uuid4

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.api_routes.admission import admission_rejected, publish_event
from event.event_schema import Event
from event.codec import source_event_identity
from core.chat_mode import configure_chat_llm
from core.llm_adapter import get_llm
from runtime.request_persistence import RequestAdmissionError

router = APIRouter()


class ChatRequest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    source_event_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    mode: Literal["normal", "deep"] = "normal"
    remember: bool = Field(
        default=False,
        strict=True,
        description="Explicitly retain this user message in long-term memory as a user statement.",
    )


def _chat_event(
    req: ChatRequest, correlation_id: str, *, stream: bool = False
) -> Event:
    data = req.model_dump(exclude={"schema_version", "source_event_id"})
    if stream:
        data["stream"] = True
    identity: dict[str, Any] = (
        {"id": source_event_identity("user", "user_message", req.source_event_id)}
        if req.source_event_id
        else {}
    )
    return Event(
        **identity,
        type="user_message",
        source="user",
        payload=data,
        correlation_id=correlation_id,
        source_event_id=req.source_event_id,
    )


# ── unified async endpoint (returns immediately) ──────────────


@router.get("/api/chat/modes")
def chat_modes() -> dict[str, Any]:
    llm = get_llm()
    return {
        "modes": [
            configure_chat_llm(llm, mode).mode_info for mode in ("normal", "deep")
        ]
    }


@router.post("/api/chat")
def chat(req: ChatRequest, request: Request) -> Any:
    """Publish a user message and return immediately with a task_id.

    The client receives the result via WebSocket ``chat_reply`` channel
    or polls ``GET /api/chat/result/{task_id}``.
    """
    logger = logging.getLogger("eva.api.chat")
    container = request.app.state.container
    start = time.perf_counter()

    correlation_id = str(uuid4())
    event = _chat_event(req, correlation_id)
    if reservation_error := _reserve(container, event):
        return reservation_error
    if rejection := publish_event(container, event):
        return _queue_rejected(container, correlation_id, rejection)
    container.system_state["pending_events"] = container.event_bus.size()
    container.system_state["pending_results"] = container.result_registry.size()

    queue_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "chat queued event_id=%s correlation_id=%s queue_ms=%s",
        event.id,
        correlation_id,
        queue_ms,
    )

    return {
        "accepted": True,
        "task_id": correlation_id,
        "event_id": event.id,
        "mode": req.mode,
        "request_deadline_sec": container.result_registry.pending_timeout_sec,
        "result_retention_sec": container.result_registry.retention_sec,
        "message": "event queued; listen on WS chat_reply or poll /api/chat/result/{task_id}",
    }


# ── SSE receipt endpoint (full processing pipeline) ──────────


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> Any:
    """SSE of the canonical reviewed receipt, with bounded request lifetime.

    The registry is authoritative even if WebSocket delivery is unavailable.
    The named result event carries failure/uncertainty, followed by [DONE].
    Disconnecting a stream does not cancel the admitted request.
    """
    logger = logging.getLogger("eva.api.chat_stream")
    container = request.app.state.container
    correlation_id = str(uuid4())
    event = _chat_event(req, correlation_id, stream=True)
    if reservation_error := await asyncio.to_thread(_reserve, container, event):
        return reservation_error
    if rejection := publish_event(container, event):
        return _queue_rejected(container, correlation_id, rejection)
    container.system_state["pending_events"] = container.event_bus.size()

    async def event_generator() -> AsyncGenerator[str, None]:
        last_heartbeat = time.monotonic()
        try:
            while True:
                view = await asyncio.to_thread(
                    container.result_registry.lookup, correlation_id
                )
                if view["state"] == "terminal":
                    result = view["payload"]
                    if result.get("reply"):
                        yield f"data: {_sse_escape(result['reply'])}\n\n"
                    yield (
                        "event: result\ndata: "
                        + json.dumps(
                            {**result, "completed": True},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    yield "data: [DONE]\n\n"
                    return
                if view["state"] == "missing":
                    yield 'event: result\ndata: {"completed": false, "error": "result_unknown_or_expired"}\n\n'
                    yield "data: [DONE]\n\n"
                    return
                if time.monotonic() - last_heartbeat >= 15.0:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.debug(
                "SSE disconnected; task retained correlation_id=%s", correlation_id
            )
            raise
        except Exception:
            logger.exception("SSE delivery failed correlation_id=%s", correlation_id)
            yield "data: [ERROR]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-EVA-Task-ID": correlation_id,
        },
    )


# ── backward-compatible sync endpoint ─────────────────────────


@router.post("/api/chat/sync")
def chat_sync(req: ChatRequest, request: Request) -> Any:
    """Legacy sync endpoint — blocks until the cognition loop finishes.

    Kept for backward compatibility during the transition to the
    unified WS-based model. Prefer ``POST /api/chat`` + WS listener.
    """
    logger = logging.getLogger("eva.api.chat_sync")
    container = request.app.state.container
    start = time.perf_counter()

    correlation_id = str(uuid4())
    event = _chat_event(req, correlation_id)
    if reservation_error := _reserve(container, event):
        return reservation_error
    if rejection := publish_event(container, event):
        return _queue_rejected(container, correlation_id, rejection)
    container.system_state["pending_events"] = container.event_bus.size()
    container.system_state["pending_results"] = container.result_registry.size()

    result = container.result_registry.wait(
        correlation_id=correlation_id,
        timeout=settings.request_timeout_sec,
    )

    if result is None:
        logger.warning("chat_sync timeout correlation_id=%s", correlation_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "accepted": True,
                "completed": False,
                "task_id": correlation_id,
                "wait_timed_out": True,
                "message": "request accepted; HTTP wait ended before a receipt was available",
            },
        )

    container.system_state["pending_results"] = container.result_registry.size()
    wait_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "chat_sync done correlation_id=%s agent=%s wait_ms=%s",
        correlation_id,
        result.get("selected_agent", ""),
        wait_ms,
    )

    return {
        "accepted": True,
        "completed": True,
        "task_id": correlation_id,
        "ok": result.get("ok", False),
        "error": result.get("error"),
        "terminal_state": result.get("terminal_state"),
        "execution_state": result.get("execution_state"),
        "review": result.get("review"),
        "mode": result.get("mode", req.mode),
        "mode_info": result.get("mode_info"),
        "memory_write_ids": result.get("memory_write_ids", {}),
        "reply": result.get("reply", ""),
        "reply_available": result.get("reply_available", True),
        "selected_agent": result.get("selected_agent", ""),
        "loop_id": result.get("loop_id", ""),
        "duration_ms": result.get("duration_ms", 0),
    }


# ── poll fallback ─────────────────────────────────────────────


@router.get("/api/chat/result/{task_id}")
def get_chat_result(task_id: str, request: Request) -> Any:
    """Poll for a pending chat result by task_id.

    Returns the completed result if available, or 202 if still pending.
    Use this only as a fallback — the primary delivery mechanism is
    WebSocket ``chat_reply``.
    """
    container = request.app.state.container
    try:
        view = container.result_registry.lookup(task_id)
    except (ValueError, RuntimeError, sqlite3.Error):
        return JSONResponse(
            status_code=503,
            content={
                "completed": False,
                "task_id": task_id,
                "error": "durable_request_unavailable",
            },
        )
    if view["state"] == "missing":
        return JSONResponse(
            status_code=404,
            content={
                "completed": False,
                "task_id": task_id,
                "error": "result_unknown_or_expired",
                "detail": "No retained receipt exists for this task.",
            },
        )
    result = view["payload"]
    if view["state"] != "terminal":
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "completed": False,
                "task_id": task_id,
                "state": view["state"],
                "timing": view["timing"],
            },
        )
    return {
        "completed": True,
        "timing": view["timing"],
        "task_id": task_id,
        "ok": result.get("ok", False),
        "error": result.get("error"),
        "terminal_state": result.get("terminal_state"),
        "execution_state": result.get("execution_state"),
        "review": result.get("review"),
        "mode": result.get("mode", "normal"),
        "mode_info": result.get("mode_info"),
        "memory_write_ids": result.get("memory_write_ids", {}),
        "reply": result.get("reply", ""),
        "reply_available": result.get("reply_available", True),
        "selected_agent": result.get("selected_agent", ""),
        "loop_id": result.get("loop_id", ""),
        "duration_ms": result.get("duration_ms", 0),
    }


# ── state endpoint ────────────────────────────────────────────


@router.get("/api/state")
def get_state(request: Request) -> dict[str, Any]:
    container = request.app.state.container
    ss = container.system_state

    ss["pending_events"] = container.event_bus.size()
    ss["pending_results"] = container.result_registry.size()

    # ── memory stats ────────────────────────────────────────
    memory_stats: dict[str, Any] = {}
    tiered = getattr(container, "tiered_memory", None)
    if tiered is not None:
        try:
            memory_stats = tiered.stats()
        except Exception:
            memory_stats = {"error": "unavailable"}

    result = {
        "focus": ss["focus"],
        "mode": ss["mode"],
        "active_tasks": ss["active_tasks"],
        "pending_events": ss["pending_events"],
        "events_dropped": container.event_bus.dropped,
        "pending_results": ss.get("pending_results", 0),
        "result_registry": container.result_registry.stats(),
        "last_reply": ss["last_reply"],
        "last_selected_agent": ss["last_selected_agent"],
        "last_loop_id": ss["last_loop_id"],
        "last_loop_at": ss["last_loop_at"],
        "last_snapshot_at": ss.get("last_snapshot_at"),
        "last_proactive_reason": ss.get("last_proactive_reason"),
        "last_memory_governor": ss.get("last_memory_governor"),
        "last_context_summary": ss.get("last_context_summary"),
        "scheduler_running": ss.get("scheduler_running", False),
        "agents": ss.get("agents", []),
        "stability_score": ss.get("stability_score", 1.0),
        "mean_prediction_error": ss.get("mean_prediction_error", 0.0),
        "total_perturbations": ss.get("total_perturbations", 0),
        "memory_stats": memory_stats,
    }

    if ss.get("minimal_brain") is not None:
        result["minimal_brain"] = ss["minimal_brain"]

    # Attachment is independent of HTTP execution. No unmeasured tick is invented.
    if "mvsc_status" in ss:
        result["mvsc"] = dict(ss["mvsc_status"])
    controller = container.runtime.controller
    result["runtime_mode"] = controller.mode if controller else "unknown"

    return result


# ── internal helpers ──────────────────────────────────────────


def _queue_rejected(container: Any, correlation_id: str, reason: str) -> JSONResponse:
    """Undo request-local bookkeeping when the bus declines admission."""
    container.result_registry.reject_unpublished(correlation_id, reason)
    container.system_state["pending_events"] = container.event_bus.size()
    container.system_state["pending_results"] = container.result_registry.size()
    return admission_rejected(reason)


def _reserve(container: Any, event: Event) -> JSONResponse | None:
    try:
        if not container.result_registry.create(
            event.correlation_id, event_id=event.id, event=event
        ):
            return admission_rejected("result_capacity_full")
    except RequestAdmissionError as error:
        if error.reason == "durable_request_conflict":
            return JSONResponse(
                status_code=409,
                content={
                    "accepted": False,
                    "error": error.reason,
                    "task_id": error.task_id,
                    "event_id": error.event_id,
                    "detail": "This source event already has a registered task. Poll its receipt instead of executing it again.",
                },
            )
        return admission_rejected(error.reason)
    return None


def _sse_escape(text: str) -> str:
    return text.replace("\n", "\\n").replace("\r", "")
