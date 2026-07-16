from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PersonaProfile(BaseModel):
    persona_id: str
    name: str
    role_definition: str
    tone_style: str
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    value_weights: dict[str, float] = Field(default_factory=dict)
    version: int
    updated_at: datetime
    confidence: float = 0.8
    source_event_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        return self.model_dump()
