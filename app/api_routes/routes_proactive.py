from fastapi import APIRouter, Request

from event.event_schema import Event


router = APIRouter()


@router.get("/api/proactive/state")
def proactive_state(request: Request) -> dict:
    container = request.app.state.container
    return {
        "proactive_state": container["proactive_state"],
        "last_proactive_reason": container["system_state"].get("last_proactive_reason"),
    }


@router.post("/api/debug/maintenance/trigger")
def trigger_maintenance(request: Request) -> dict:
    container = request.app.state.container
    event_bus = container["event_bus"]

    event = Event(
        type="maintenance",
        source="debug_api",
        payload={"kind": "manual_maintenance"},
    )
    event_bus.publish(event)

    return {
        "ok": True,
        "message": "maintenance event queued",
        "pending_events": event_bus.size(),
    }
