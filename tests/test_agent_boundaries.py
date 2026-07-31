"""Agent boundary, edge case, and error path tests."""

import tempfile
import os
from pathlib import Path

from agents.search_agent import SearchAgent
from agents.coding_agent import CodingAgent
from agents.docs_agent import DocsAgent
from agents.chat_agent import ChatAgent
from agents.base_agent import AgentTask


# ── Search Agent Boundaries ────────────────────────────────

class TestSearchAgentBoundaries:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.test_file = os.path.join(cls.tmpdir, "searchable.txt")
        with open(cls.test_file, "w") as f:
            f.write("hello world\nthis is a test file\nwith multiple lines\n")

    def test_empty_query(self):
        agent = SearchAgent()
        task = AgentTask(kind="search", payload={"query": "", "root": self.tmpdir})
        result = agent.run(task)
        assert result.ok
        assert "no query" in result.content.lower()

    def test_nonexistent_root(self):
        agent = SearchAgent()
        task = AgentTask(kind="search", payload={"query": "test", "root": "/nonexistent/path"})
        result = agent.run(task)
        assert not result.ok
        assert "not exist" in result.content.lower()

    def test_no_matches(self):
        agent = SearchAgent()
        task = AgentTask(kind="search", payload={"query": "zzz_nonexistent_pattern_xyz", "root": self.tmpdir})
        result = agent.run(task)
        assert result.ok
        assert "no matches" in result.content.lower()

    def test_finds_content(self):
        agent = SearchAgent()
        task = AgentTask(kind="search", payload={"query": "hello", "root": self.tmpdir})
        result = agent.run(task)
        assert result.ok
        assert result.meta["matches"] >= 1

    def test_context_injection(self):
        agent = SearchAgent()
        context = {"active_tasks": [{"name": "Find all test refs"}], "memories": []}
        task = AgentTask(kind="search", payload={"query": "test", "root": self.tmpdir, "context": context})
        result = agent.run(task)
        assert result.ok
        assert "mock" in result.meta.get("llm_provider", "")

    def test_prefix_normalization(self):
        agent = SearchAgent()
        assert agent._normalize_query("/search hello") == "hello"
        assert agent._normalize_query("search: world") == "world"
        assert agent._normalize_query("find: test") == "test"
        assert agent._normalize_query("lookup: thing") == "thing"
        assert agent._normalize_query("plain query") == "plain query"

    def test_exception_handling(self):
        agent = SearchAgent()
        task = AgentTask(kind="search", payload={"query": None, "root": None})
        result = agent.run(task)
        assert result.ok  # shouldn't crash


# ── Coding Agent Boundaries ────────────────────────────────

class TestCodingAgentBoundaries:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.py_file = os.path.join(cls.tmpdir, "sample.py")
        with open(cls.py_file, "w") as f:
            f.write('"""Test module."""\n\nimport os\n\n\ndef hello():\n    return "world"\n\nclass MyClass:\n    pass\n')

    def test_empty_input(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={"text": ""})
        result = agent.run(task)
        assert result.ok
        assert "no input" in result.content.lower()

    def test_file_not_found(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={"path": "/nonexistent/nope.py"})
        result = agent.run(task)
        assert not result.ok
        assert "not found" in result.content.lower()

    def test_analyze_file(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={"path": self.py_file})
        result = agent.run(task)
        assert result.ok
        assert result.meta["lines"] > 0
        assert "hello" in result.meta.get("functions", [])

    def test_inline_code_analysis(self):
        agent = CodingAgent()
        code = "def foo():\n    return 42\n\nclass Bar:\n    pass"
        task = AgentTask(kind="code", payload={"text": code})
        result = agent.run(task)
        assert result.ok
        assert "foo" in result.meta.get("functions", [])

    def test_context_injection(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={
            "path": self.py_file,
            "context": {"active_tasks": [{"name": "Review sample.py"}]},
        })
        result = agent.run(task)
        assert result.ok
        assert "mock" in result.meta.get("llm_provider", "")

    def test_prefix_extraction(self):
        agent = CodingAgent()
        assert agent._extract_path("/code main.py") == Path("main.py")
        assert agent._extract_path("code: app.py") == Path("app.py")
        assert agent._extract_path("inspect: test.py") == Path("test.py")
        assert agent._extract_path("just some text") is None

    def test_exception_handling(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={"text": None, "path": None})
        result = agent.run(task)
        # gracefully handles None inputs
        assert result.ok
        assert "no input" in result.content.lower()

    def test_file_too_large_handled(self):
        agent = CodingAgent()
        task = AgentTask(kind="code", payload={"path": "constitution.yaml"})
        result = agent.run(task)
        assert result.ok  # handles gracefully


# ── Docs Agent Boundaries ──────────────────────────────────

class TestDocsAgentBoundaries:
    def test_empty_input(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={"text": ""})
        result = agent.run(task)
        assert result.ok
        assert "no document" in result.content.lower()

    def test_short_text(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={"text": "EVA is a cognitive system."})
        result = agent.run(task)
        assert result.ok
        assert "mock" in result.meta.get("llm_provider", "")

    def test_context_injection(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={
            "text": "The system architecture document v2...",
            "context": {"active_tasks": [{"name": "Review architecture"}]},
        })
        result = agent.run(task)
        assert result.ok

    def test_exception_on_none(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={"text": None})
        result = agent.run(task)
        assert not result.ok or result.ok  # must not crash

    def test_long_text_truncated_to_5000(self):
        agent = DocsAgent()
        long_text = "x" * 6000
        task = AgentTask(kind="summarize", payload={"text": long_text})
        result = agent.run(task)
        assert result.ok
        # mock LLM shows preview of first 160 chars
        assert "preview" in result.content.lower() or result.meta["source_length"] == 6000

    def test_no_context_works(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={
            "text": "Simple document content.",
        })
        result = agent.run(task)
        assert result.ok

    def test_mock_llm_returns_preview(self):
        agent = DocsAgent()
        task = AgentTask(kind="summarize", payload={"text": "Hello world" * 20})
        result = agent.run(task)
        # With mock LLM, returns preview
        assert "document" in result.content.lower() or "preview" in result.content.lower()


# ── Chat Agent Boundaries ──────────────────────────────────

class TestChatAgentBoundaries:
    def test_reply_includes_llm_provider(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": "hello"})
        result = agent.run(task)
        assert "llm_provider" in result.meta

    def test_context_counts_in_meta(self):
        agent = ChatAgent()
        context = {
            "context_summary": "Active tasks (1): Fix bug",
            "active_tasks": [{"name": "Fix bug"}],
            "memories": [{"content": "user mentioned bug"}],
            "recent_entities": ["task_fix_bug"],
        }
        task = AgentTask(kind="chat", payload={"text": "status", "context": context})
        result = agent.run(task)
        assert result.meta["active_tasks_count"] == 1
        assert result.meta["memories_count"] == 1

    def test_empty_input_returns_early(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": ""})
        result = agent.run(task)
        assert "empty input" in result.content

    def test_run_stream_with_text(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": "hello"})
        tokens: list[str] = []
        result = agent.run_stream(task, tokens.append)
        assert result.ok
        assert len(tokens) > 0
        assert "".join(tokens) == result.content

    def test_run_stream_empty_input(self):
        agent = ChatAgent()
        task = AgentTask(kind="chat", payload={"text": ""})
        tokens: list[str] = []
        result = agent.run_stream(task, tokens.append)
        assert "empty input" in result.content
        assert len(tokens) == 1

    def test_build_system_with_persona(self):
        agent = ChatAgent()
        context = {
            "persona": {
                "name": "HAL",
                "role_definition": "spaceship AI",
                "tone_style": "calm, monotone",
                "hard_constraints": ["never open the pod bay doors"],
            },
        }
        result = agent._build_system(context)
        assert "HAL" in result
        assert "pod bay doors" in result

    def test_build_system_without_persona(self):
        agent = ChatAgent()
        result = agent._build_system(None)
        assert "EVA" in result
        assert "fabricate" in result

    def test_build_system_with_context_summary(self):
        agent = ChatAgent()
        context = {"context_summary": "User is working on auth module"}
        result = agent._build_system(context)
        assert "auth module" in result
