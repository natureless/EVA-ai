from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from event.event_schema import Event


router = APIRouter()


@router.get("/api/proactive/state")
def proactive_state(request: Request) -> dict[str, Any]:
    container = request.app.state.container
    return {
        "proactive_state": container.proactive_state,
        "last_proactive_reason": container.system_state.get("last_proactive_reason"),
    }


@router.post("/api/debug/maintenance/trigger")
def trigger_maintenance(request: Request) -> dict[str, Any]:
    container = request.app.state.container
    event = Event(
        type="maintenance",
        source="debug_api",
        payload={"kind": "manual_maintenance"},
    )
    container.event_bus.publish(event)
    return {
        "ok": True,
        "message": "maintenance event queued",
        "pending_events": container.event_bus.size(),
    }
