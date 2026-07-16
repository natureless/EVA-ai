import json
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException

from app.config import settings
from runtime.diagnostics import SystemDiagnostic

router = APIRouter()


@router.get("/health/live")
def health_live(request: Request) -> dict:
    return request.app.state.container.health.live()


@router.get("/health/ready")
def health_ready(request: Request) -> dict:
    return request.app.state.container.health.ready()


@router.get("/health/diagnostic")
def health_diagnostic(request: Request) -> dict:
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
async def health_recover(request: Request) -> dict:
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
def websocket_stats(request: Request) -> dict:
    """WebSocket connection statistics."""
    ws = request.app.state.container.ws_manager
    return ws.stats() if ws else {"connections": 0}
