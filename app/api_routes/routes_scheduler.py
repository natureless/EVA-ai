from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/scheduler/jobs")
def scheduler_jobs(request: Request) -> dict[str, Any]:
    """列出所有调度任务。"""
    return {"jobs": request.app.state.container.scheduler.list_jobs()}


@router.get("/api/scheduler/stats")
def scheduler_stats(request: Request) -> dict[str, Any]:
    """调度器统计信息。"""
    container = request.app.state.container
    ss = container.system_state

    return {
        "running": ss.get("scheduler_running", False),
        "ready": ss.get("scheduler_ready", False),
        "tick_interval_sec": container.settings.scheduler_tick_interval_sec,
        "maintenance_interval_sec": container.settings.scheduler_maintenance_interval_sec,
        "snapshot_interval_sec": container.settings.scheduler_snapshot_interval_sec,
        "last_snapshot_at": ss.get("last_snapshot_at"),
        "last_proactive_reason": ss.get("last_proactive_reason"),
    }
