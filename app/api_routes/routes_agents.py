from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/agents")
def list_agents(request: Request) -> dict[str, Any]:
    return {"agents": request.app.state.container.registry.list_agents()}
