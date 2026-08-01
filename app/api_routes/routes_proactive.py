from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from event.event_schema import Event


router = APIRouter()


@router.get("/api/proactive/state")
def proactive_state(request: Request) -> dict[str, Any]:
    """主动引擎状态 — 停滞检测和提醒。"""
    import time

    container = request.app.state.container
    ps = container.proactive_state
    now = time.time()

    last_msg = ps.get("last_user_message_ts")
    idle_sec = int(now - last_msg) if last_msg else 0

    return {
        "proactive_state": ps,  # backward compatible
        "last_proactive_reason": container.system_state.get("last_proactive_reason"),
        "idle_seconds": idle_sec,
        "idle_hours": round(idle_sec / 3600, 1),
        "stagnation_detected": idle_sec >= container.settings.stagnation_threshold_sec,
        "threshold_hours": round(container.settings.stagnation_threshold_sec / 3600, 1),
        "reminder_cooldown_hours": round(container.settings.reminder_cooldown_sec / 3600, 1),
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
