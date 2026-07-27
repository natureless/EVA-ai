"""LLM adapter unit + integration tests."""

import os
from core.llm_adapter import (
    get_llm,
    MockLLM,
    OpenAIAdapter,
    ClaudeAdapter,
    LLMAdapter,
)


class TestLLMAdapter:
    """Test the LLM adapter factory and individual adapters."""

    def test_get_llm_mock_fallback(self):
        """When no API key is set, returns MockLLM."""
        # Save and clear env
        saved = {}
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "EVA_LLM_PROVIDER"):
            saved[key] = os.environ.pop(key, None)

        try:
            llm = get_llm()
            assert isinstance(llm, MockLLM)
            assert llm.provider == "mock"
        finally:
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val
                elif key in os.environ:
                    del os.environ[key]

    def test_get_llm_explicit_provider(self):
        llm = get_llm(provider="openai", api_key="sk-test")
        assert isinstance(llm, OpenAIAdapter)
        assert "openai" in llm.provider

    def test_get_llm_claude(self):
        llm = get_llm(provider="claude", api_key="sk-ant-test")
        assert isinstance(llm, ClaudeAdapter)
        assert "anthropic" in llm.provider

    def test_mock_chat(self):
        llm = MockLLM()
        reply = llm.chat([
            {"role": "system", "content": "You are EVA."},
            {"role": "user", "content": "Hello world"},
        ])
        assert "Hello world" in reply
        assert "[EVA mock]" in reply

    def test_mock_chat_with_context(self):
        llm = MockLLM()
        reply = llm.chat([
            {"role": "system", "content": "You are EVA."},
            {"role": "user", "content": "Fix the login bug. Context:\nActive tasks (1): Fix login bug"},
        ])
        assert "context-aware" in reply

    def test_openai_no_api_key_falls_back(self):
        adapter = OpenAIAdapter(api_key="")
        reply = adapter.chat([{"role": "user", "content": "test"}])
        assert "mock" in reply.lower() or "EVA" in reply

    def test_claude_no_api_key_falls_back(self):
        adapter = ClaudeAdapter(api_key="")
        reply = adapter.chat([{"role": "user", "content": "test"}])
        assert "mock" in reply.lower() or "EVA" in reply

    def test_adapter_is_llm_adapter(self):
        assert isinstance(MockLLM(), LLMAdapter)
        assert isinstance(OpenAIAdapter(api_key="x"), LLMAdapter)
        assert isinstance(ClaudeAdapter(api_key="x"), LLMAdapter)

    def test_messages_preserved(self):
        """Messages structure is valid JSON-serializable."""
        import json
        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "test user"},
        ]
        json_str = json.dumps(messages)
        assert "system" in json_str
        assert "user" in json_str


class TestChatAgentWithLLM:
    """Verify ChatAgent uses LLM adapter correctly."""

    def test_chat_agent_with_mock(self):
        from agents.chat_agent import ChatAgent
        from agents.base_agent import AgentTask

        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={
            "text": "explain quantum computing",
        })
        result = agent.run(task)
        assert result.ok
        assert result.meta["llm_provider"] == "mock"
        assert "quantum computing" in result.content.lower()

    def test_chat_agent_context_passed(self):
        from agents.chat_agent import ChatAgent
        from agents.base_agent import AgentTask

        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={
            "text": "status update",
            "context": {
                "context_summary": "Active tasks (2): Deploy v2, Fix nav",
                "active_tasks": [
                    {"name": "Deploy v2", "status": "active"},
                    {"name": "Fix nav", "status": "active"},
                ],
                "memories": [
                    {"content": "deployment pipeline configured yesterday"},
                ],
                "recent_entities": ["task_deploy_v2", "task_fix_nav"],
            },
        })
        result = agent.run(task)
        assert result.ok
        assert result.meta["has_context"] is True
        assert result.meta["active_tasks_count"] == 2

    def test_empty_input(self):
        from agents.chat_agent import ChatAgent
        from agents.base_agent import AgentTask

        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": ""})
        result = agent.run(task)
        assert "empty input" in result.content
