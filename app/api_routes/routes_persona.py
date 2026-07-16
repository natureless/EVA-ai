import logging

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
def persona_active(request: Request) -> dict:
    profile = request.app.state.container.persona_service.get_active_persona()
    return {"persona": profile.model_dump()}


@router.post("/api/persona/update")
def persona_update(req: PersonaUpdateRequest, request: Request) -> dict:
    logger = logging.getLogger("eva.api.persona")
    profile = request.app.state.container.persona_service.update_profile(
        req.model_dump(exclude_unset=True)
    )
    logger.info("persona updated version=%s", profile.version)
    return {"persona": profile.model_dump()}
