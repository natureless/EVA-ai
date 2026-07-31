"""Unit tests for ContextBuilder."""

from unittest.mock import MagicMock

import pytest

from core.context_builder import ContextBuilder


class TestContextBuilder:
    def test_build_no_services(self):
        builder = ContextBuilder()
        result = builder.build(user_id="user1", text="hello")
        assert "persona" in result
        assert result["persona"] is None
        assert result["memories"] == []
        assert result["active_tasks"] == []
        assert result["context_summary"] == ""

    def test_build_with_persona(self):
        persona_svc = MagicMock()
        persona_svc.get_active_persona.return_value.model_dump.return_value = {
            "name": "EVA", "role_definition": "assistant",
        }
        builder = ContextBuilder(persona_service=persona_svc)
        result = builder.build(user_id="user1", text="hello")
        assert result["persona"]["name"] == "EVA"

    def test_build_with_memories(self):
        tiered = MagicMock()
        tiered.recall.return_value = [
            {"id": "m1", "content": "user prefers dark mode"},
            {"id": "m2", "content": "working on auth module"},
        ]
        builder = ContextBuilder(tiered_memory=tiered)
        result = builder.build(user_id="user1", text="preferences")
        assert len(result["memories"]) == 2
        assert "dark mode" in result["context_summary"]

    def test_build_with_active_tasks(self):
        wm = MagicMock()
        wm.active_tasks = [{"name": "Fix login bug"}, {"name": "Deploy v2"}]
        wm.recent_entities = ["task_1", "user_alice"]
        builder = ContextBuilder(world_model=wm)
        result = builder.build(user_id="user1", text="status")
        assert len(result["active_tasks"]) == 2
        assert "Active tasks" in result["context_summary"]

    def test_build_empty_text_skips_memory_recall(self):
        tiered = MagicMock()
        builder = ContextBuilder(tiered_memory=tiered)
        result = builder.build(user_id="user1", text="")
        tiered.recall.assert_not_called()
