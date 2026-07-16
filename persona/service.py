from datetime import datetime, timezone
from typing import Any

from persona.models import PersonaProfile
from persona.repository import PersonaRepository


class PersonaService:
    def __init__(self, repo: PersonaRepository) -> None:
        self.repo = repo

    def get_active_persona(self) -> PersonaProfile:
        profile = self.repo.get_active()
        if profile:
            return profile

        default_profile = PersonaProfile(
            persona_id="default-persona",
            name="EVA",
            role_definition="A persistent cognitive assistant with stable identity.",
            tone_style="precise, calm, concise",
            hard_constraints=[
                "do not fabricate memories",
                "do not overclaim certainty",
                "do not violate explicit user boundaries",
            ],
            soft_preferences=[
                "favor clarity over ornament",
                "favor continuity over novelty",
            ],
            value_weights={
                "truthfulness": 1.0,
                "stability": 0.9,
                "helpfulness": 0.8,
            },
            version=1,
            updated_at=datetime.now(timezone.utc),
            confidence=0.9,
        )
        self.repo.upsert(default_profile)
        return default_profile

    def update_profile(self, patch: dict[str, Any]) -> PersonaProfile:
        profile = self.get_active_persona()

        for field in [
            "name",
            "role_definition",
            "tone_style",
            "hard_constraints",
            "soft_preferences",
            "value_weights",
        ]:
            if field in patch and patch[field] is not None:
                setattr(profile, field, patch[field])

        if "confidence" in patch and patch["confidence"] is not None:
            profile.confidence = float(patch["confidence"])
        if "source_event_id" in patch:
            profile.source_event_id = patch.get("source_event_id")

        profile.version += 1
        profile.updated_at = datetime.now(timezone.utc)
        self.repo.upsert(profile)
        return profile

    def render_system_prompt(self, context: dict[str, Any]) -> str:
        persona = context.get("persona") or self.get_active_persona()
        return (
            f"Name: {persona.name}\n"
            f"Role: {persona.role_definition}\n"
            f"Tone: {persona.tone_style}\n"
            f"Hard constraints: {persona.hard_constraints}\n"
            f"Soft preferences: {persona.soft_preferences}\n"
        )

    def validate_response(self, reply: str) -> list[str]:
        violations = []
        text = reply.lower()
        if "100% certain" in text:
            violations.append("overclaiming certainty")
        return violations
