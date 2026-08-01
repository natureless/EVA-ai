from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, HTTPException

from app.config import settings
from runtime.diagnostics import SystemDiagnostic

router = APIRouter()


@router.get("/health/live")
def health_live(request: Request) -> dict[str, Any]:
    # Starlette's request.app.state is untyped at runtime
    return request.app.state.container.health.live()  # type: ignore[no-any-return]


@router.get("/health/ready")
def health_ready(request: Request) -> dict[str, Any]:
    return request.app.state.container.health.ready()  # type: ignore[no-any-return]


@router.get("/health/diagnostic")
def health_diagnostic(request: Request) -> dict[str, Any]:
    """Run a full system diagnostic and return the report."""
    container = request.app.state.container
    diag = SystemDiagnostic().run_full(
        container.store,
        container.tiered_memory,
        container.system_state,
        snapshot_path=Path(settings.latest_snapshot_path),
    )
    container.system_state["diagnostic"] = diag.to_dict()
    return diag.to_dict()


@router.post("/health/recover")
async def health_recover(request: Request) -> dict[str, Any]:
    """Execute a recovery action.

    Body: {"action": "reset_policy" | "clear_registry" | "rebuild_db" | "rebuild_snapshot"}
    """
    try:
        body = await request.body()
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    action = payload.get("action", "")
    if not action:
        raise HTTPException(status_code=400, detail="missing 'action' field")

    container = request.app.state.container
    recovery = container.recovery_actions
    if recovery is None:
        raise HTTPException(status_code=503, detail="recovery actions not available")

    if action == "reset_policy":
        result = recovery.reset_policy(container.policy_engine)
    elif action == "clear_registry":
        result = recovery.clear_stale_registry(container.result_registry)
    elif action == "rebuild_db":
        result = recovery.recover_db(container.store)
    elif action == "rebuild_snapshot":
        result = recovery.recover_snapshot(
            Path(settings.latest_snapshot_path),
            world_model=container.world_model,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")

    return {
        "ok": result.passed,
        "name": result.name,
        "detail": result.detail,
        "recommendation": result.recommendation,
    }


@router.get("/health/ws")
def websocket_stats(request: Request) -> dict[str, Any]:
    """WebSocket connection statistics."""
    ws = request.app.state.container.ws_manager
    return ws.stats() if ws else {"connections": 0}


@router.get("/health/summary")
def health_summary(request: Request) -> dict[str, Any]:
    """聚合健康摘要 — 适合仪表板使用。"""
    container = request.app.state.container
    ss = container.system_state
    ready_data = container.health.ready()

    return {
        "status": ready_data["status"],
        "uptime_events": container.event_bus.size(),
        "events_dropped": container.event_bus.dropped,
        "cognition": {
            "focus": ss.get("focus", "idle"),
            "tick": ss.get("mvsc_tick", 0),
            "phase": ss.get("mvsc_cognition_phase", "unknown"),
        },
        "memory": {
            "working": len(container.tiered_memory.s2.list_recent(limit=1)) if container.tiered_memory else 0,
        },
        "agents": len(container.registry.list_agents()),
        "components_ready": sum(1 for v in ready_data.get("components", {}).values() if v is True),
        "components_total": len(ready_data.get("components", {})),
    }


@router.get("/health/ws/channels")
def ws_channels(request: Request) -> dict[str, Any]:
    """WebSocket 频道列表和连接数。"""
    ws = request.app.state.container.ws_manager
    if ws is None:
        return {"channels": {}, "total": 0}
    stats = ws.stats()
    return {
        "channels": stats.get("channels", {}),
        "total_connections": stats.get("total_connections", 0),
        "messages_sent": stats.get("messages_sent", 0),
    }


@router.get("/health/rate-limiter")
def rate_limiter_stats(request: Request) -> dict[str, Any]:
    """速率限制器统计。"""
    limiter = request.app.state.rate_limiter
    if limiter is None:
        return {"status": "unavailable"}
    try:
        return {"status": "active", "stats": limiter.stats()}
    except Exception:
        return {"status": "error"}
