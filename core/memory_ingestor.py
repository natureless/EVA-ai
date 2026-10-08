"""Creates MemoryRecord + ImportanceFeatures for cognition loop events."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from memory.importance_scorer import ImportanceFeatures
from memory.memory_governor import MemoryGovernor
from memory.memory_schema import MemoryRecord, MemoryType
from memory.provenance import EpistemicStatus, provenance


class MemoryIngestor:
    """Extracts memory records from user messages with importance scoring.

    Keeps the cognition loop thin by isolating the record-creation and
    feature-extraction logic that would otherwise be ~20 lines inline.
    """

    def __init__(self, memory_governor: MemoryGovernor | None, self_model: dict[str, Any] | None) -> None:
        self._governor = memory_governor
        self._self_model = self_model

    def ingest_user_message(
        self,
        text: str,
        event_id: str,
        source: str,
        active_tasks: list[dict[str, Any]],
        *,
        self_model_delta: float = 0.0,
        prediction_error: float = 0.0,
        remember: bool = False,
    ) -> MemoryRecord | None:
        """Create and ingest an episodic memory record for a user message."""
        if not self._governor:
            return

        record = MemoryRecord(
            id=str(uuid4()),
            memory_type=MemoryType.EPISODIC,
            content=text,
            source_event_id=event_id,
            confidence=0.7,
            ttl_seconds=None,
            conflict_keys=[],
            metadata={
                "provenance": provenance(
                    EpistemicStatus.USER_STATEMENT if source == "user" else EpistemicStatus.UNKNOWN,
                    source=source, source_event_id=event_id,
                ),
                # Only trusted request data supplies consent, never model output.
                "long_term_allowed": remember is True and source == "user",
            },
        )

        features = ImportanceFeatures(
            user_explicit=text.startswith("/"),
            goal_related=self._text_matches_active_tasks(text, active_tasks),
            blocker_related=False,
            persona_related=self._text_matches_persona(text),
            repeated_mentions=0,
            source_reliability=0.9 if source == "user" else 0.6,
            emotional_intensity=self._estimate_emotional_intensity(text),
            age_hours=0.0,
            self_model_delta=self_model_delta,
            prediction_error=prediction_error,
        )

        return self._governor.ingest(record, features)

    # ── helpers ────────────────────────────────────────────────────

    def _text_matches_active_tasks(self, text: str, active_tasks: list[dict[str, Any]]) -> bool:
        if not active_tasks:
            return False
        text_lower = text.lower()
        for task in active_tasks:
            desc = str(task.get("description", "")).lower()
            name = str(task.get("name", "")).lower()
            if desc and desc in text_lower:
                return True
            if name and name in text_lower:
                return True
        return False

    def _text_matches_persona(self, text: str) -> bool:
        if not self._self_model:
            return False
        text_lower = text.lower()
        for cap in self._self_model.get("capabilities", []):
            if str(cap).lower() in text_lower:
                return True
        identity = str(self._self_model.get("identity", "")).lower()
        if identity and identity in text_lower:
            return True
        return False

    @staticmethod
    def _estimate_emotional_intensity(text: str) -> float:
        markers_high = ["urgent", "紧急", "asap", "!!!", "critical", "严重"]
        markers_med = ["worried", "担心", "frustrated", "important", "重要"]
        text_lower = text.lower()
        if any(m in text_lower for m in markers_high):
            return 0.8
        if any(m in text_lower for m in markers_med):
            return 0.4
        return 0.1
