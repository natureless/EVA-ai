from typing import Any


class ContextBuilder:
    def __init__(
        self,
        *,
        persona_service=None,
        memory_service=None,
        user_model_service=None,
        world_model_service=None,
    ) -> None:
        self.persona_service = persona_service
        self.memory_service = memory_service
        self.user_model_service = user_model_service
        self.world_model_service = world_model_service

    def build(self, *, user_id: str, text: str) -> dict[str, Any]:
        persona = (
            self.persona_service.get_active_persona()
            if self.persona_service
            else None
        )
        memories = (
            self.memory_service.retrieve(text, top_k=5) if self.memory_service else []
        )
        user_profile = (
            self.user_model_service.get_user_profile(user_id)
            if self.user_model_service
            else None
        )
        user_state = (
            self.user_model_service.infer_current_state(user_id)
            if self.user_model_service
            else None
        )
        world_context = (
            self.world_model_service.get_active_context(text)
            if self.world_model_service
            else {"nodes": [], "edges": []}
        )

        return {
            "persona": persona,
            "memories": memories,
            "user_profile": user_profile,
            "user_state": user_state,
            "world_context": world_context,
        }
