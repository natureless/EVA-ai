"""Chat API routes — unified event-driven execution model.

Both sync and streaming paths publish events to the EventBus and
return immediately. Results are delivered via WebSocket channels:

- ``chat_token``  — per-token streaming output
- ``chat_reply``  — final agent response

Poll fallback: ``GET /api/chat/result/{task_id}`` for clients that
cannot open a WebSocket connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from event.event_schema import Event

router = APIRouter()


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ── unified async endpoint (returns immediately) ──────────────

@router.post("/api/chat")
def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
    """Publish a user message and return immediately with a task_id.

    The client receives the result via WebSocket ``chat_reply`` channel
    or polls ``GET /api/chat/result/{task_id}``.
    """
    logger = logging.getLogger("eva.api.chat")
    container = request.app.state.container
    start = time.perf_counter()

    correlation_id = str(uuid4())
    container.result_registry.create(correlation_id)

    event = Event(
        type="user_message",
        source="user",
        payload={"text": req.text},
        correlation_id=correlation_id,
    )
    container.event_bus.publish(event)
    container.system_state["pending_events"] = container.event_bus.size()
    container.system_state["pending_results"] = container.result_registry.size()

    queue_ms = int((time.perf_counter() - start) * 1000)
    logger.info("chat queued event_id=%s correlation_id=%s queue_ms=%s",
                event.id, correlation_id, queue_ms)

    return {
        "accepted": True,
        "task_id": correlation_id,
        "event_id": event.id,
        "message": "event queued; listen on WS chat_reply or poll /api/chat/result/{task_id}",
    }


# ── SSE streaming endpoint (WS-backed, full pipeline) ─────────

@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> Any:
    """SSE endpoint backed by the unified cognition pipeline.

    Publishes a ``user_message`` event with ``stream: true``, then
    subscribes to WebSocket ``chat_token`` and ``chat_reply`` channels
    and forwards tokens as Server-Sent Events.

    This replaces the old direct-to-LLM SSE path — the full
    Planner → Router → Policy → Agent pipeline runs, and tokens
    arrive via WS broadcast.
    """
    logger = logging.getLogger("eva.api.chat_stream")
    container = request.app.state.container
    ws = container.ws_manager

    if ws is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "WebSocket manager not available"},
        )

    correlation_id = str(uuid4())
    container.result_registry.create(correlation_id)

    token_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Track whether we've received the final reply so we can
    # stop the SSE stream cleanly.
    stream_done = asyncio.Event()

    async def _on_chat_token(channel: str, payload: dict[str, Any]) -> None:
        if payload.get("task_id") == correlation_id:
            await token_queue.put(payload.get("token", ""))

    async def _on_chat_reply(channel: str, payload: dict[str, Any]) -> None:
        if payload.get("task_id") == correlation_id:
            stream_done.set()

    ws.subscribe("chat_token", _on_chat_token)
    ws.subscribe("chat_reply", _on_chat_reply)

    event = Event(
        type="user_message",
        source="user",
        payload={"text": req.text, "stream": True},
        correlation_id=correlation_id,
    )
    container.event_bus.publish(event)
    container.system_state["pending_events"] = container.event_bus.size()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while not stream_done.is_set():
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=0.1)
                    if token is None:
                        break
                    if token:
                        yield f"data: {_sse_escape(token)}\n\n"
                except asyncio.TimeoutError:
                    continue
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.debug("SSE stream cancelled correlation_id=%s", correlation_id)
        except Exception:
            logger.exception("SSE stream error correlation_id=%s", correlation_id)
            yield "data: [ERROR]\n\n"
        finally:
            ws.unsubscribe("chat_token", _on_chat_token)
            ws.unsubscribe("chat_reply", _on_chat_reply)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
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
    container.result_registry.create(correlation_id)

    event = Event(
        type="user_message",
        source="user",
        payload={"text": req.text},
        correlation_id=correlation_id,
    )
    container.event_bus.publish(event)
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
                "message": "event queued but timed out waiting for result",
            },
        )

    container.result_registry.pop(correlation_id)
    container.system_state["pending_results"] = container.result_registry.size()
    wait_ms = int((time.perf_counter() - start) * 1000)
    logger.info("chat_sync done correlation_id=%s agent=%s wait_ms=%s",
                correlation_id, result.get("selected_agent", ""), wait_ms)

    return {
        "accepted": True,
        "completed": True,
        "task_id": correlation_id,
        "reply": result.get("reply", ""),
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
    result = container.result_registry.peek(task_id)
    if result is None:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"completed": False, "task_id": task_id},
        )
    container.result_registry.pop(task_id)
    return {
        "completed": True,
        "task_id": task_id,
        "reply": result.get("reply", ""),
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

    return {
        "focus": ss["focus"],
        "mode": ss["mode"],
        "active_tasks": ss["active_tasks"],
        "pending_events": ss["pending_events"],
        "events_dropped": container.event_bus.dropped,
        "pending_results": ss.get("pending_results", 0),
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
    }


# ── internal helpers ──────────────────────────────────────────

def _sse_escape(text: str) -> str:
    return text.replace("\n", "\\n").replace("\r", "")
