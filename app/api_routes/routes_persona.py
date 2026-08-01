from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter()


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    role_definition: str | None = None
    tone_style: str | None = None
    hard_constraints: list[str] | None = None
    soft_preferences: list[str] | None = None
    value_weights: dict[str, float] | None = None
    confidence: float | None = None
    source_event_id: str | None = None


@router.get("/api/persona/active")
def persona_active(request: Request) -> dict[str, Any]:
    profile = request.app.state.container.persona_service.get_active_persona()
    return {"persona": profile.model_dump()}


@router.post("/api/persona/update")
def persona_update(req: PersonaUpdateRequest, request: Request) -> dict[str, Any]:
    logger = logging.getLogger("eva.api.persona")
    profile = request.app.state.container.persona_service.update_profile(
        req.model_dump(exclude_unset=True)
    )
    logger.info("persona updated version=%s", profile.version)
    return {"persona": profile.model_dump()}


@router.get("/api/persona/stats")
def persona_stats(request: Request) -> dict[str, Any]:
    """人格统计 — 活跃版本和更新历史。"""
    container = request.app.state.container
    profile = container.persona_service.get_active_persona()

    return {
        "name": profile.name,
        "version": profile.version,
        "role_definition": profile.role_definition[:200] if profile.role_definition else "",
        "tone_style": profile.tone_style,
        "constraints_count": len(profile.hard_constraints or []),
        "preferences_count": len(profile.soft_preferences or []),
    }
