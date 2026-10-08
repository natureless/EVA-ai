from datetime import datetime
from typing import Any

from memory.storage_adapter import BaseStorageAdapter
from persona.models import PersonaProfile


class PersonaRepository:
    def __init__(self, store: BaseStorageAdapter) -> None:
        self.store = store

    def get_active(self) -> PersonaProfile | None:
        row = self.store.fetchone(
            """
            SELECT persona_id, name, role_definition, tone_style, hard_constraints,
                   soft_preferences, value_weights, version, updated_at, confidence, source_event_id
            FROM persona_profiles
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        if not row:
            return None
        return self._row_to_profile(row)

    def upsert(self, profile: PersonaProfile) -> None:
        payload = profile.model_dump()
        self.store.execute(
            """
            INSERT OR REPLACE INTO persona_profiles (
                persona_id, name, role_definition, tone_style,
                hard_constraints, soft_preferences, value_weights,
                version, updated_at, confidence, source_event_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["persona_id"],
                payload["name"],
                payload["role_definition"],
                payload["tone_style"],
                self.store.dumps_json(payload["hard_constraints"]),
                self.store.dumps_json(payload["soft_preferences"]),
                self.store.dumps_json(payload["value_weights"]),
                payload["version"],
                payload["updated_at"].isoformat(),
                payload["confidence"],
                payload["source_event_id"],
            ),
        )

    def _row_to_profile(self, row: dict[str, Any]) -> PersonaProfile:
        return PersonaProfile(
            persona_id=row["persona_id"],
            name=row["name"],
            role_definition=row["role_definition"],
            tone_style=row["tone_style"],
            hard_constraints=self.store.loads_json(row["hard_constraints"] or "[]"),
            soft_preferences=self.store.loads_json(row["soft_preferences"] or "[]"),
            value_weights=self.store.loads_json(row["value_weights"] or "{}"),
            version=int(row["version"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            confidence=float(row.get("confidence", 0.8)),
            source_event_id=row.get("source_event_id"),
        )
