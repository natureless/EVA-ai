from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/scheduler/jobs")
def scheduler_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": request.app.state.container.scheduler.list_jobs()}
