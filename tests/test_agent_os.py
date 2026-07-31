"""Unit tests for agent_os — registry, router, orchestrator, task converter."""

from unittest.mock import MagicMock

import pytest

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from agent_os.registry import AgentRegistry
from agent_os.router import AgentRouter
from agent_os.orchestrator import AgentOrchestrator
from agent_os.agent_task import task_to_executor_params, AGENT_EXECUTOR_MAP


class FakeAgent(BaseAgent):
    """Minimal agent stub for testing."""
    def __init__(self, name: str, kind: str = "chat"):
        self.name = name
        self.description = f"Fake {name}"
        self._kind = kind

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == self._kind

    def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(ok=True, agent=self.name, content=f"reply from {self.name}",
                           summary="ok", meta={})


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = FakeAgent("chat_agent")
        reg.register(agent)
        assert reg.get("chat_agent") is agent

    def test_get_missing_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_agents_sorted(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("b"))
        reg.register(FakeAgent("a"))
        assert reg.list_agents() == ["a", "b"]


class TestAgentRouter:
    def test_route_preferred_can_handle(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("chat_agent", kind="chat"))
        router = AgentRouter(reg)
        task = AgentTask(kind="chat", payload={})
        result = router.route("chat_agent", task)
        assert result == "chat_agent"

    def test_route_preferred_cannot_handle_falls_back(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("chat_agent", kind="chat"))
        reg.register(FakeAgent("search_agent", kind="search"))
        router = AgentRouter(reg)
        task = AgentTask(kind="search", payload={})
        result = router.route("chat_agent", task)
        assert result == "search_agent"

    def test_route_fallback_to_first(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("only_agent", kind="chat"))
        router = AgentRouter(reg)
        task = AgentTask(kind="search", payload={})
        result = router.route("only_agent", task)
        assert result == "only_agent"  # fallback


class TestAgentOrchestrator:
    def test_execute_direct(self):
        reg = AgentRegistry()
        agent = FakeAgent("chat_agent")
        reg.register(agent)
        orch = AgentOrchestrator(reg)
        task = AgentTask(kind="chat", payload={"text": "hello"})
        result, duration = orch.execute("chat_agent", task)
        assert result.ok is True
        assert result.agent == "chat_agent"
        assert duration >= 0

    def test_execute_missing_agent_raises(self):
        reg = AgentRegistry()
        orch = AgentOrchestrator(reg)
        task = AgentTask(kind="chat", payload={})
        with pytest.raises(ValueError):
            orch.execute("nonexistent", task)

    def test_execute_gated_with_token(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("search_agent", kind="search"))
        orch = AgentOrchestrator(reg)

        executor = MagicMock()
        executor.name = "file"
        executor.validate_token.return_value = MagicMock(allowed=True, reason="ok")
        executor.check_boundaries.return_value = MagicMock(allowed=True, reason="ok")
        executor.audit_log = MagicMock()

        tm = MagicMock()
        task = AgentTask(kind="search", payload={"text": "find"})
        result, duration = orch.execute("search_agent", task, executor=executor,
                                         token_manager=tm, token_id="tok-1")
        assert result.ok is True
        executor.validate_token.assert_called_once()
        executor.check_boundaries.assert_called_once()

    def test_execute_gated_token_denied(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("search_agent", kind="search"))
        orch = AgentOrchestrator(reg)

        executor = MagicMock()
        executor.name = "file"
        executor.validate_token.return_value = MagicMock(allowed=False, reason="expired")
        executor.audit_log = MagicMock()

        tm = MagicMock()
        task = AgentTask(kind="search", payload={"text": "find"})
        result, duration = orch.execute("search_agent", task, executor=executor,
                                         token_manager=tm, token_id="tok-1")
        assert result.ok is False
        assert "token denied" in result.content

    def test_execute_gated_boundary_denied(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("search_agent", kind="search"))
        orch = AgentOrchestrator(reg)

        executor = MagicMock()
        executor.name = "file"
        executor.validate_token.return_value = MagicMock(allowed=True)
        executor.check_boundaries.return_value = MagicMock(allowed=False, reason="path blocked")
        executor.audit_log = MagicMock()

        task = AgentTask(kind="search", payload={"text": "find"})
        result, duration = orch.execute("search_agent", task, executor=executor,
                                         token_manager=MagicMock(), token_id="tok-1")
        assert result.ok is False
        assert "boundary denied" in result.content

    def test_execute_stream(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("chat_agent"))
        orch = AgentOrchestrator(reg)
        task = AgentTask(kind="chat", payload={"text": "hello"})
        tokens: list[str] = []
        result, duration = orch.execute_stream("chat_agent", task, tokens.append)
        assert result.ok is True
        assert len(tokens) > 0

    def test_execute_stream_blocked_for_gated_agent(self):
        reg = AgentRegistry()
        reg.register(FakeAgent("search_agent", kind="search"))
        orch = AgentOrchestrator(reg)
        task = AgentTask(kind="search", payload={"text": "find"})
        tokens: list[str] = []
        result, duration = orch.execute_stream("search_agent", task, tokens.append)
        assert result.ok is False
        assert "streaming denied" in result.content


class TestTaskToExecutorParams:
    def test_file_search(self):
        task = AgentTask(kind="search", payload={"query": "config", "root": "/app"})
        result = task_to_executor_params(task, "file")
        assert result["action"] == "search"
        assert result["params"]["query"] == "config"

    def test_file_code(self):
        task = AgentTask(kind="code", payload={"path": "main.py"})
        result = task_to_executor_params(task, "file")
        assert result["action"] == "inspect"
        assert result["params"]["path"] == "main.py"

    def test_file_summarize(self):
        task = AgentTask(kind="summarize", payload={"text": "doc content"})
        result = task_to_executor_params(task, "file")
        assert result["action"] == "inspect"

    def test_comms_log(self):
        task = AgentTask(kind="chat", payload={"text": "hello"})
        result = task_to_executor_params(task, "comms")
        assert result["action"] == "log"
        assert result["params"]["message"] == "hello"

    def test_unknown_executor(self):
        task = AgentTask(kind="chat", payload={"text": "hi"})
        result = task_to_executor_params(task, "unknown")
        assert result["action"] == "agent_task"

    def test_agent_executor_map(self):
        assert AGENT_EXECUTOR_MAP["search_agent"] == "file"
        assert AGENT_EXECUTOR_MAP["coding_agent"] == "file"
        assert AGENT_EXECUTOR_MAP["docs_agent"] == "file"
