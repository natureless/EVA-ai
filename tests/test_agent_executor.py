"""Agent-executor integration tests."""

import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent_os.agent_task import (
    AGENT_EXECUTOR_MAP,
    task_to_executor_params,
)
from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agents.base_agent import AgentTask, AgentResult
from agents.search_agent import SearchAgent
from agents.coding_agent import CodingAgent
from agents.chat_agent import ChatAgent
from core.executor import (
    ExecutorAuditLog,
    FileExecutor,
    BaseExecutor,
    ExecutorDecision,
)
from memory.sqlite_store import SQLiteStore


# ── Task Conversion ─────────────────────────────────────────

class TestAgentTaskConversion:
    def test_map_search_to_file(self):
        params = task_to_executor_params(
            AgentTask(kind="search", payload={"query": "hello", "root": "."}),
            "file",
        )
        assert params["action"] == "search"
        assert params["params"]["kind"] == "search"
        assert params["params"]["query"] == "hello"

    def test_map_code_to_file(self):
        params = task_to_executor_params(
            AgentTask(kind="code", payload={"path": "/tmp/test.py"}),
            "file",
        )
        assert params["action"] == "inspect"
        assert params["params"]["kind"] == "code"

    def test_unknown_executor_passthrough(self):
        params = task_to_executor_params(
            AgentTask(kind="chat", payload={"text": "hello"}),
            "unknown",
        )
        assert params["action"] == "agent_task"

    def test_agent_executor_map(self):
        assert AGENT_EXECUTOR_MAP["search_agent"] == "file"
        assert AGENT_EXECUTOR_MAP["coding_agent"] == "file"
        assert "chat_agent" not in AGENT_EXECUTOR_MAP
        assert "docs_agent" not in AGENT_EXECUTOR_MAP


# ── Orchestrator with Executor ─────────────────────────────

class TestOrchestratorExecutorDelegation:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()
        cls.audit = ExecutorAuditLog(cls.store)
        cls.test_file = os.path.join(cls.tmpdir, "test.txt")
        with open(cls.test_file, "w") as f:
            f.write("hello from orchestrator test")

    def test_search_agent_via_file_executor(self):
        registry = AgentRegistry()
        registry.register(SearchAgent())
        orchestrator = AgentOrchestrator(registry)

        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        task = AgentTask(kind="search", payload={
            "query": "orchestrator",
            "root": self.tmpdir,
        })

        result, duration = orchestrator.execute(
            "search_agent", task, executor=executor,
        )
        assert result.ok
        assert "orchestrator" in result.content

    def test_chat_agent_no_executor(self):
        registry = AgentRegistry()
        registry.register(ChatAgent())
        orchestrator = AgentOrchestrator(registry)

        task = AgentTask(kind="chat", payload={"text": "hello"})
        result, duration = orchestrator.execute("chat_agent", task)
        assert result.ok
        assert "chat_agent" in result.agent

    def test_executor_denial_returned_as_agent_error(self):
        registry = AgentRegistry()
        registry.register(SearchAgent())
        orchestrator = AgentOrchestrator(registry)

        # FileExecutor with restricted path
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": ["/nonexistent"]}}
        })
        task = AgentTask(kind="search", payload={
            "query": "secret",
            "root": "/etc",
        })

        result, duration = orchestrator.execute(
            "search_agent", task, executor=executor,
        )
        assert not result.ok
        assert "denied" in result.content.lower()

    def test_executor_audit_recorded(self):
        audit = ExecutorAuditLog(self.store)
        executor = FileExecutor(audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        registry = AgentRegistry()
        registry.register(SearchAgent())
        orchestrator = AgentOrchestrator(registry)

        task = AgentTask(kind="search", payload={
            "query": "audit_test",
            "root": self.tmpdir,
        })
        orchestrator.execute("search_agent", task, executor=executor)

        items = audit.query(executor_type="file", limit=10)
        assert len(items) >= 1
        assert items[0]["status"] == "success"
        assert "audit_test" in items[0]["parameters"].get("query", "")


# ── Agent-Executor mapping validation ──────────────────────

class TestAgentExecutorMapping:
    def test_search_agent_mapped_to_file(self):
        assert AGENT_EXECUTOR_MAP["search_agent"] == "file"

    def test_coding_agent_mapped_to_file(self):
        assert AGENT_EXECUTOR_MAP["coding_agent"] == "file"

    def test_chat_not_mapped(self):
        assert "chat_agent" not in AGENT_EXECUTOR_MAP

    def test_docs_not_mapped(self):
        assert "docs_agent" not in AGENT_EXECUTOR_MAP
