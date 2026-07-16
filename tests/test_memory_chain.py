"""Cross-session memory chain integration tests."""

import tempfile
import os
from pathlib import Path

from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from world.world_model import WorldModelGraph
from core.context_builder import ContextBuilder
from agents.chat_agent import ChatAgent
from agents.base_agent import AgentTask


# ── Context Builder with Tiers ─────────────────────────────

class TestContextBuilderWithTiers:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_recall_from_s2(self):
        tm = TieredMemoryManager(self.store)
        tm.ingest("remember the project deadline is Friday", importance=0.7, source="user")

        cb = ContextBuilder(tiered_memory=tm)
        ctx = cb.build(user_id="default", text="deadline")

        assert ctx["context_summary"] != ""
        assert len(ctx["memories"]) >= 1
        assert "deadline" in ctx["context_summary"].lower()

    def test_empty_with_no_history(self):
        tm = TieredMemoryManager(self.store)
        cb = ContextBuilder(tiered_memory=tm)
        ctx = cb.build(user_id="default", text="nothing")

        assert ctx["context_summary"] == ""
        assert ctx["memories"] == []

    def test_active_tasks_in_context(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Implement login", {"status": "active"})
        wm.upsert_entity("task", "Fix nav bug", {"status": "active"})

        cb = ContextBuilder(world_model=wm)
        ctx = cb.build(user_id="default", text="login")

        assert len(ctx["active_tasks"]) == 2
        assert "Implement login" in ctx["context_summary"]

    def test_recent_entities_in_context(self):
        wm = WorldModelGraph()
        wm.upsert_entity("person", "Alice", {})
        wm.upsert_entity("person", "Bob", {})
        wm.upsert_entity("task", "Deploy v2", {"status": "active"})

        cb = ContextBuilder(world_model=wm)
        ctx = cb.build(user_id="default", text="deploy")

        assert len(ctx["recent_entities"]) >= 2
        assert "Deploy v2" in ctx["context_summary"]
        assert "Alice" in ctx["recent_entities"]

    def test_combined_context(self):
        tm = TieredMemoryManager(self.store)
        tm.ingest("user wants dark mode", importance=0.85, source="user")

        wm = WorldModelGraph()
        wm.upsert_entity("task", "Add dark mode", {"status": "active"})

        cb = ContextBuilder(tiered_memory=tm, world_model=wm)
        ctx = cb.build(user_id="default", text="dark mode design")

        assert "dark mode" in ctx["context_summary"].lower()
        assert "Add dark mode" in ctx["context_summary"]


# ── Chat Agent with Context ────────────────────────────────

class TestChatAgentWithContext:
    def test_reply_with_context(self):
        agent = ChatAgent()
        context = {
            "context_summary": "Active tasks (2): Fix bug, Deploy",
            "active_tasks": [
                {"name": "Fix bug", "status": "active"},
                {"name": "Deploy", "status": "active"},
            ],
            "memories": [
                {"content": "user asked about bug fixes yesterday"},
            ],
            "recent_entities": ["task_fix_bug", "task_deploy"],
        }
        task = AgentTask(kind="chat", payload={"text": "hello", "context": context})
        result = agent.run(task)

        assert result.ok
        assert result.meta["has_context"] is True
        assert result.meta["active_tasks_count"] == 2
        assert "hello" in result.content.lower()

    def test_reply_without_context(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": "hi"})
        result = agent.run(task)

        assert result.ok
        assert "hi" in result.content
        assert "Context" not in result.content

    def test_empty_input(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": ""})
        result = agent.run(task)

        assert "empty input" in result.content


# ── End-to-End Memory Chain ────────────────────────────────

class TestMemoryChainE2E:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_tasks_persist_across_sessions(self):
        """First session creates tasks → second session sees them."""
        # Session 1: create tasks
        wm1 = WorldModelGraph()
        wm1.upsert_entity("task", "Session 1 task", {"status": "active"})
        wm1.upsert_entity("person", "Session user", {})

        tm1 = TieredMemoryManager(self.store)
        tm1.ingest("task created in session 1", importance=0.9, source="user")

        # Flush to storage
        wm1.flush(tm1.s4)

        # Session 2: load from storage
        wm2 = WorldModelGraph()
        wm2.load_from_store(tm1.s4)

        cb = ContextBuilder(tiered_memory=tm1, world_model=wm2)
        ctx = cb.build(user_id="default", text="session 1")

        assert "Session 1 task" in ctx["context_summary"]
        assert len(ctx["active_tasks"]) >= 1
        assert len(ctx["memories"]) >= 1

    def test_cross_restart_persistence(self):
        """Entities persist after 'restart' (new store load)."""
        store2 = SQLiteStore(Path(os.path.join(self.tmpdir, "restart.db")))
        store2.init_db()

        # Session 1
        tm_a = TieredMemoryManager(store2)
        wm_a = WorldModelGraph()
        wm_a.upsert_entity("task", "Persistent task", {"priority": "high"})
        wm_a.flush(tm_a.s4)
        tm_a.ingest("important: add dark mode", importance=0.95, source="user")

        # "Restart" — new objects, same store
        tm_b = TieredMemoryManager(store2)
        wm_b = WorldModelGraph()
        wm_b.load_from_store(tm_b.s4)

        cb = ContextBuilder(tiered_memory=tm_b, world_model=wm_b)
        ctx = cb.build(user_id="default", text="dark mode")

        assert "Persistent task" in ctx["context_summary"]
        assert "dark mode" in ctx["context_summary"].lower()
