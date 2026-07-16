"""Context builder — assembles relevant history for each cognition event.

V2 (enable_v02_pipeline): gathers context from persona, tiered memory (S2+S3),
world model (S4), and assembles a structured context dict + summary string
that agents can use to produce historically-grounded responses.
"""

from typing import Any


class ContextBuilder:
    def __init__(
        self,
        *,
        persona_service=None,
        tiered_memory=None,
        world_model=None,
    ) -> None:
        self.persona_service = persona_service
        self.tiered_memory = tiered_memory
        self.world_model = world_model

    def build(self, *, user_id: str, text: str) -> dict[str, Any]:
        persona = (
            self.persona_service.get_active_persona().model_dump()
            if self.persona_service
            else None
        )

        # ── recall from tiered memory (S2 + S3) ─────────────
        memories: list[dict] = []
        if self.tiered_memory and text.strip():
            memories = self.tiered_memory.recall(text, tiers=[2, 3])
            memories = memories[:10]

        # ── world model: active tasks + recent entities ──────
        active_tasks: list[dict] = []
        recent_entities: list[str] = []
        if self.world_model:
            active_tasks = self.world_model.active_tasks[:10]
            recent_entities = self.world_model.recent_entities[:10]

        # ── build summary ────────────────────────────────────
        parts = []
        if active_tasks:
            names = [t.get("name", "") for t in active_tasks if t.get("name")]
            parts.append(f"Active tasks ({len(active_tasks)}): " + ", ".join(names[:5]))
        if memories:
            snippets = [m.get("content", "")[:60] for m in memories[:3]]
            parts.append(f"Recent memories ({len(memories)}): " + "; ".join(snippets))
        if recent_entities:
            parts.append(f"Entities ({len(recent_entities)}): " + ", ".join(recent_entities[:5]))

        context_summary = "\n".join(parts) if parts else ""

        return {
            "persona": persona,
            "memories": memories,
            "active_tasks": active_tasks,
            "recent_entities": recent_entities,
            "context_summary": context_summary,
        }
