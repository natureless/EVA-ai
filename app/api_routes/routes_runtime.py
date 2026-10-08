"""Runtime observations independent of the selected cognition implementation."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from app.runtime_observation import runtime_observation

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("")
def runtime_state(request: Request) -> dict[str, Any]:
    controller = request.app.state.container.runtime.controller
    if controller is None:
        raise HTTPException(status_code=503, detail="runtime controller unavailable")
    return runtime_observation(request.app.state.container)
