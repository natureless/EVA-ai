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
    event_bus = container["event_bus"]
    system_state = container["system_state"]
    result_registry = container["result_registry"]
    start = time.perf_counter()

    correlation_id = str(uuid4())
    result_registry.create(correlation_id)

    event = Event(
        type="user_message",
        source="user",
        payload={"text": req.text},
        correlation_id=correlation_id,
    )
    event_bus.publish(event)
    system_state["pending_events"] = event_bus.size()
    system_state["pending_results"] = result_registry.size()

    result = result_registry.wait(
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

    result_registry.pop(correlation_id)
    system_state["pending_results"] = result_registry.size()
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
    system_state = container["system_state"]
    event_bus = container["event_bus"]
    result_registry = container["result_registry"]

    system_state["pending_events"] = event_bus.size()
    system_state["pending_results"] = result_registry.size()

    return {
        "focus": system_state["focus"],
        "mode": system_state["mode"],
        "active_tasks": system_state["active_tasks"],
        "pending_events": system_state["pending_events"],
        "pending_results": system_state.get("pending_results", 0),
        "last_reply": system_state["last_reply"],
        "last_selected_agent": system_state["last_selected_agent"],
        "last_loop_id": system_state["last_loop_id"],
        "last_loop_at": system_state["last_loop_at"],
        "last_snapshot_at": system_state.get("last_snapshot_at"),
        "last_proactive_reason": system_state.get("last_proactive_reason"),
        "last_memory_governor": system_state.get("last_memory_governor"),
        "last_context_summary": system_state.get("last_context_summary"),
        "scheduler_running": system_state.get("scheduler_running", False),
        "agents": system_state.get("agents", []),
    }
