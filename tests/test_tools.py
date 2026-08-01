"""Tests for tool registry and ChatAgent tool-calling."""
import json
import sys
sys.path.insert(0, '.')

from core.tool_registry import (
    ToolRegistry, ToolDef, create_builtin_tools, execute_tool, format_tool_result,
)
from agents.chat_agent import ChatAgent


def test_tool_registry_register():
    registry = ToolRegistry()
    tool = ToolDef(
        name="test_tool",
        description="A test tool",
        parameters={"type": "object", "properties": {}},
        handler=lambda **kw: {"ok": True, "summary": "done"},
    )
    registry.register(tool)
    assert registry.get("test_tool") is tool
    assert len(registry.list_all()) == 1
    assert "test_tool" in registry.list_names()


def test_create_builtin_tools():
    tools = create_builtin_tools()
    names = {t.name for t in tools}
    assert "search_files" in names
    assert "read_file" in names
    assert "list_directory" in names
    assert "run_code" in names
    assert "web_fetch" in names


def test_execute_search_files():
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    result = execute_tool("search_files", {"query": "ChatAgent", "root": "."}, registry)
    assert result["ok"] is True
    assert "matches" in result


def test_execute_read_file():
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    result = execute_tool("read_file", {"path": "README.md", "max_lines": 3}, registry)
    assert result["ok"] is True
    assert "content" in result


def test_execute_list_directory():
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    result = execute_tool("list_directory", {"path": ".", "max_entries": 3}, registry)
    assert result["ok"] is True
    assert "entries" in result


def test_execute_run_code():
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    result = execute_tool("run_code", {"code": "print(42)", "language": "python"}, registry)
    assert result["ok"] is True
    assert "42" in result.get("stdout", "")


def test_execute_run_code_dangerous_blocked():
    """Dangerous code patterns are blocked before execution."""
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    # os.system is blocked
    result = execute_tool("run_code", {"code": "import os; os.system('ls')", "language": "python"}, registry)
    assert result["ok"] is False
    assert "blocked for safety" in result["error"]

    # subprocess is blocked
    result = execute_tool("run_code", {"code": "import subprocess; subprocess.run(['ls'])", "language": "python"}, registry)
    assert result["ok"] is False
    assert "blocked for safety" in result["error"]

    # rm -rf is blocked
    result = execute_tool("run_code", {"code": "rm -rf /tmp/foo", "language": "bash"}, registry)
    assert result["ok"] is False
    assert "blocked for safety" in result["error"]


def test_execute_unknown_tool():
    registry = ToolRegistry()
    result = execute_tool("nonexistent", {}, registry)
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_format_tool_result_search():
    result = {
        "ok": True,
        "summary": "Found 2 matches",
        "matches": [
            {"file": "a.py", "line": 1, "content": "hello world"},
        ],
    }
    formatted = format_tool_result("search_files", result)
    assert "search_files" in formatted
    assert "a.py" in formatted


def test_format_tool_result_error():
    result = {"ok": False, "error": "something went wrong"}
    formatted = format_tool_result("read_file", result)
    assert "error" in formatted
    assert "something went wrong" in formatted


def test_tool_call_parse_none():
    assert ChatAgent._parse_tool_call("Just a normal response") is None
    assert ChatAgent._parse_tool_call("") is None


def test_tool_call_parse_bare_json():
    text = '{"tool": "search_files", "args": {"query": "test"}}'
    result = ChatAgent._parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "search_files"
    assert result["args"] == {"query": "test"}


def test_tool_call_parse_fenced():
    text = '```tool\n{"tool": "read_file", "args": {"path": "x.py"}}\n```'
    result = ChatAgent._parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "read_file"
    assert result["args"] == {"path": "x.py"}


def test_tool_call_parse_embedded():
    text = 'Let me look that up:\n\n```tool\n{"tool": "search_files", "args": {"query": "hello"}}\n```\n\nNow checking...'
    result = ChatAgent._parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "search_files"


def test_chat_agent_with_tools():
    registry = ToolRegistry()
    for t in create_builtin_tools():
        registry.register(t)

    agent = ChatAgent(tool_registry=registry)
    system = agent._build_system({})
    assert "Available tools" in system
    assert "search_files" in system
    assert "read_file" in system


def test_chat_agent_without_tools():
    agent = ChatAgent(tool_registry=None)
    system = agent._build_system({})
    assert "Available tools" not in system


def test_openai_schema():
    tool = ToolDef(
        name="test",
        description="Test tool",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        handler=lambda **kw: {},
    )
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "test"


def test_anthropic_schema():
    tool = ToolDef(
        name="test",
        description="Test tool",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        handler=lambda **kw: {},
    )
    schema = tool.to_anthropic_schema()
    assert schema["name"] == "test"
    assert "input_schema" in schema


# ── Tool streaming progress tests ──────────────────────────

class TestToolStreamingProgress:
    """Tests for tool progress event emission during streaming."""

    def test_summarize_tool_args_search(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("search_files", {"query": "hello", "path": "/src"})
        assert "hello" in result
        assert "/src" in result

    def test_summarize_tool_args_read(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("read_file", {"path": "/etc/hosts"})
        assert "/etc/hosts" in result

    def test_summarize_tool_args_list(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("list_directory", {"path": "/tmp"})
        assert "/tmp" in result

    def test_summarize_tool_args_run_code(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("run_code", {"code": "print('hello')"})
        assert "print('hello')" in result

    def test_summarize_tool_args_long_code_truncated(self):
        agent = ChatAgent()
        long_code = "x" * 200
        result = agent._summarize_tool_args("run_code", {"code": long_code})
        assert len(result) < 150

    def test_summarize_tool_args_web_fetch(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("web_fetch", {"url": "https://example.com/page"})
        assert "example.com" in result

    def test_summarize_tool_args_search_memory(self):
        agent = ChatAgent()
        result = agent._summarize_tool_args("search_memory", {"query": "project deadline"})
        assert "project deadline" in result

    def test_summarize_tool_result_search(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("search_files", {"ok": True, "results": [1, 2, 3, 4, 5]})
        assert "5" in result

    def test_summarize_tool_result_read(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("read_file", {"ok": True, "content": "a" * 500})
        assert "500" in result

    def test_summarize_tool_result_list(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("list_directory", {"ok": True, "entries": [1, 2]})
        assert "2" in result

    def test_summarize_tool_result_run_code(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("run_code", {"ok": True, "exit_code": 0})
        assert "0" in result

    def test_summarize_tool_result_web_fetch(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("web_fetch", {"ok": True, "content": "abc"})
        assert "3" in result

    def test_summarize_tool_result_memory(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("search_memory", {"ok": True, "results": [1, 2, 3]})
        assert "3" in result

    def test_summarize_tool_result_unknown(self):
        agent = ChatAgent()
        result = agent._summarize_tool_result("custom_tool", {"ok": True})
        assert result == "Done"

    def test_tool_loop_emits_progress_tokens(self):
        """Tool loop emits [TOOL_START] and [TOOL_RESULT] progress events."""
        from core.llm_adapter import MockLLM
        from core.tool_registry import ToolRegistry, ToolDef

        # Create a mock LLM that returns a tool call then an answer
        mock_llm = MockLLM()
        # Override chat to return tool call first, then answer
        call_count = [0]
        def mock_chat(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return '```tool\n{"tool": "echo_test", "args": {"text": "hello"}}\n```'
            return "Final answer after tool use."
        mock_llm.chat = mock_chat

        # Register a test tool
        registry = ToolRegistry()
        registry.register(ToolDef(
            name="echo_test",
            description="Echo test tool",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=lambda **kw: {"ok": True, "output": kw.get("text", "")},
        ))

        agent = ChatAgent(tool_registry=registry)
        messages = [{"role": "user", "content": "test"}]

        # Collect progress tokens
        progress_tokens: list[str] = []
        def on_token(token: str) -> None:
            progress_tokens.append(token)

        reply, rounds = agent._tool_loop(mock_llm, messages, on_token=on_token)

        assert rounds == 1
        assert "Final answer after tool use." in reply
        # Should have TOOL_START and TOOL_RESULT events
        tool_events = [t for t in progress_tokens if "[TOOL_" in t]
        assert len(tool_events) >= 2
        assert any("TOOL_START" in t for t in tool_events)
        assert any("TOOL_RESULT" in t for t in tool_events)

    def test_tool_loop_without_callback(self):
        """Tool loop works without on_token callback (backward compat)."""
        from core.llm_adapter import MockLLM
        from core.tool_registry import ToolRegistry, ToolDef

        mock_llm = MockLLM()
        mock_llm.chat = lambda msgs: "Direct answer, no tools."

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="dummy",
            description="Dummy tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: {"ok": True},
        ))

        agent = ChatAgent(tool_registry=registry)
        messages = [{"role": "user", "content": "hi"}]

        reply, rounds = agent._tool_loop(mock_llm, messages)
        assert "Direct answer" in reply
        assert rounds == 0

    def test_tool_error_emits_error_event(self):
        """Failed tool calls emit [TOOL_ERROR] events."""
        from core.llm_adapter import MockLLM
        from core.tool_registry import ToolRegistry, ToolDef

        mock_llm = MockLLM()
        call_count = [0]
        def mock_chat(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return '```tool\n{"tool": "bad_tool", "args": {}}\n```'
            return "Recovered after tool error."
        mock_llm.chat = mock_chat

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="bad_tool",
            description="Always fails",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: {"ok": False, "error": "Something went wrong"},
        ))

        agent = ChatAgent(tool_registry=registry)
        messages = [{"role": "user", "content": "test"}]

        progress_tokens: list[str] = []
        def on_token(token: str) -> None:
            progress_tokens.append(token)

        reply, rounds = agent._tool_loop(mock_llm, messages, on_token=on_token)

        assert rounds == 1
        assert any("TOOL_ERROR" in t for t in progress_tokens)
        assert any("bad_tool" in t for t in progress_tokens)

    def test_tool_loop_llm_error_retry(self):
        """LLM errors are retried before failing."""
        from core.llm_adapter import MockLLM
        from core.tool_registry import ToolRegistry, ToolDef

        call_count = [0]
        def mock_chat(messages):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ConnectionError("transient network error")
            return "Final answer after recovery."

        mock_llm = MockLLM()
        mock_llm.chat = mock_chat

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="dummy",
            description="Dummy",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: {"ok": True},
        ))

        agent = ChatAgent(tool_registry=registry)
        messages = [{"role": "user", "content": "test"}]

        reply, rounds = agent._tool_loop(mock_llm, messages)
        assert "Final answer" in reply
        assert call_count[0] == 3  # 2 failures + 1 success

    def test_tool_loop_llm_total_failure(self):
        """When all LLM retries fail on first round, return error."""
        from core.llm_adapter import MockLLM
        from core.tool_registry import ToolRegistry, ToolDef

        mock_llm = MockLLM()
        mock_llm.chat = lambda msgs: (_ for _ in ()).throw(ConnectionError("down"))

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="dummy", description="D",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: {"ok": True},
        ))

        agent = ChatAgent(tool_registry=registry)
        messages = [{"role": "user", "content": "test"}]

        reply, rounds = agent._tool_loop(mock_llm, messages)
        assert "LLM communication error" in reply
        assert rounds == 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
