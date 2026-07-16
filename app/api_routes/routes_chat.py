from uuid import uuid4
import logging
import time

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from event.event_schema import Event


router = APIRouter()


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/api/chat")
def chat(req: ChatRequest, request: Request):
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

    result = container.result_registry.wait(
        correlation_id=correlation_id,
        timeout=settings.request_timeout_sec,
    )

    if result is None:
        logger.warning(
            "chat timeout event_id=%s correlation_id=%s",
            event.id,
            correlation_id,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "accepted": True,
                "completed": False,
                "event_id": event.id,
                "correlation_id": correlation_id,
                "message": "event queued but timed out waiting for result",
            },
        )

    container.result_registry.pop(correlation_id)
    container.system_state["pending_results"] = container.result_registry.size()
    wait_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "chat completed event_id=%s correlation_id=%s agent=%s wait_ms=%s",
        event.id,
        correlation_id,
        result.get("selected_agent", ""),
        wait_ms,
    )

    return {
        "accepted": True,
        "completed": True,
        "event_id": event.id,
        "correlation_id": correlation_id,
        "reply": result.get("reply", ""),
        "selected_agent": result.get("selected_agent", ""),
        "loop_id": result.get("loop_id", ""),
        "duration_ms": result.get("duration_ms", 0),
    }


@router.get("/api/state")
def get_state(request: Request) -> dict:
    container = request.app.state.container
    ss = container.system_state

    ss["pending_events"] = container.event_bus.size()
    ss["pending_results"] = container.result_registry.size()

    return {
        "focus": ss["focus"],
        "mode": ss["mode"],
        "active_tasks": ss["active_tasks"],
        "pending_events": ss["pending_events"],
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
    }
