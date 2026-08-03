"""Unit tests for Planner semantic routing and embedding integration."""

import numpy as np

from core.planner import Planner, _best_keyword_match
from event.event_schema import Event


class MockEmbeddingService:
    """Mock embedding service that returns pre-defined vectors."""

    def __init__(self, vector_map: dict[str, np.ndarray] | None = None) -> None:
        self._vectors = vector_map or {}
        self.encode_single_calls: list[str] = []

    def encode_single(self, text: str) -> np.ndarray:
        self.encode_single_calls.append(text)
        if text in self._vectors:
            return self._vectors[text]
        # Default: return a unit vector pointing toward "chat"
        return np.array([1.0, 0.0, 0.0], dtype=np.float64)


def _event(text: str) -> Event:
    return Event(type="user_message", source="test", payload={"text": text})


class TestPlannerSemanticRouting:
    """Tests for Planner with mock embedding service."""

    def test_no_embedding_service_falls_back_to_keywords(self):
        """Without embedding service, semantic routing is skipped."""
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("grep the config file"))
        # Falls through to keyword match
        assert plan.agent == "search_agent"
        assert plan.decision == "act"

    def test_embedding_unavailable_graceful_degrade(self):
        """When embedding build fails, fall back to keywords."""
        svc = MockEmbeddingService()
        # Make encode_single raise so _build_embeddings fails
        svc.encode_single = lambda text: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[assignment]
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("grep the config file"))
        # Falls through to keyword match
        assert plan.agent == "search_agent"

    def test_semantic_route_to_search(self):
        """Search-like query routes to search_agent via embedding."""
        svc = MockEmbeddingService({
            # User query matches search description
            "find me all python files": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            # Agent descriptions
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([1.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.5, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.2, 0.1, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("find me all python files"))
        assert plan.agent == "search_agent"
        assert plan.task.kind == "search"

    def test_semantic_route_to_coding(self):
        """Code analysis query routes to coding_agent via embedding."""
        svc = MockEmbeddingService({
            "please review this code for bugs": np.array([1.0, 0.0, 0.0], dtype=np.float64),
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([1.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.3, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.1, 0.0, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("please review this code for bugs"))
        assert plan.agent == "coding_agent"
        assert plan.task.kind == "code"

    def test_semantic_route_to_docs(self):
        """Summarization query routes to docs_agent via embedding."""
        svc = MockEmbeddingService({
            "summarize this document for me": np.array([0.5, 0.0, 0.0], dtype=np.float64),
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([0.1, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.5, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.2, 0.1, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("summarize this document for me"))
        assert plan.agent == "docs_agent"
        assert plan.task.kind == "summarize"

    def test_semantic_route_below_threshold_falls_back(self):
        """When best similarity < 0.3, fall back to keywords."""
        svc = MockEmbeddingService({
            "random gibberish xyzzy": np.array([0.0, 0.0, 0.0], dtype=np.float64),  # near-zero
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.0, 0.0, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("random gibberish xyzzy"))
        # Falls through to chat_agent (default)
        assert plan.agent == "chat_agent"

    def test_command_prefix_takes_priority_over_semantic(self):
        """Explicit /code prefix wins even when embedding says search."""
        svc = MockEmbeddingService({
            "/code README.md": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.0, 0.0, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("/code README.md"))
        # Command prefix wins
        assert plan.agent == "coding_agent"
        assert plan.task.kind == "code"

    def test_embeddings_built_only_once(self):
        """_build_embeddings is idempotent — only computes once."""
        svc = MockEmbeddingService({
            "search find files directories grep locate lookup file system": np.array([0.0, 1.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([1.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.5, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.2, 0.1, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        planner._build_embeddings()
        first_call_count = len(svc.encode_single_calls)
        planner._build_embeddings()  # second call should be no-op
        assert len(svc.encode_single_calls) == first_call_count

    def test_default_chat_routing(self):
        """General conversation routes to chat_agent even with embedding."""
        svc = MockEmbeddingService({
            "hello how are you": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "search find files directories grep locate lookup file system": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "inspect review analyze code refactor patch read file open file show file source code programming": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "summarize summary extract summarise documentation document": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "chat conversation talk discuss general question answer help": np.array([0.0, 0.0, 0.0], dtype=np.float64),
        })
        planner = Planner(embedding_service=svc)
        plan = planner.plan(_event("hello how are you"))
        assert plan.agent == "chat_agent"
        assert plan.decision == "act"


class TestPlannerKeywordMatch:
    """Tests for the keyword-based routing fallback."""

    def test_search_keyword_match(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("grep the config file"))
        assert plan.agent == "search_agent"

    def test_code_keyword_match(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("inspect and review the main module"))
        assert plan.agent == "coding_agent"

    def test_docs_keyword_match(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("summarize the meeting notes"))
        assert plan.agent == "docs_agent"

    def test_chinese_search_keywords(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("搜索日志文件"))
        assert plan.agent == "search_agent"

    def test_chinese_code_keywords(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("审查代码"))
        assert plan.agent == "coding_agent"

    def test_chinese_docs_keywords(self):
        planner = Planner(embedding_service=None)
        plan = planner.plan(_event("总结这个文档"))
        assert plan.agent == "docs_agent"

    def test_multiple_keyword_agents_picks_best(self):
        """When text matches both search and code keywords, pick the one with more hits."""
        result = _best_keyword_match(
            "find and review code file path inspect",
            ({"find", "search", "file", "path"}, "search_agent", "search"),
            ({"inspect", "review", "code"}, "coding_agent", "code"),
        )
        assert result is not None
        assert result[0] == "search_agent"  # 4 keyword hits vs 3

    def test_no_keyword_match_returns_none(self):
        result = _best_keyword_match(
            "hello world",
            ({"find", "search"}, "search_agent", "search"),
            ({"inspect", "review"}, "coding_agent", "code"),
        )
        assert result is None


class TestPlannerNonUserMessage:
    """Tests for non-user_message event routing."""

    def test_reminder_trigger(self):
        planner = Planner()
        event = Event(
            type="reminder_trigger", source="proactive",
            payload={"message": "You seem idle"},
        )
        plan = planner.plan(event)
        assert plan.decision == "act"
        assert plan.agent == "chat_agent"
        assert "idle" in plan.task.payload["text"]
        assert plan.task.payload["tools_allowed"] is False

    def test_maintenance_event(self):
        planner = Planner()
        event = Event(type="maintenance", source="scheduler", payload={})
        plan = planner.plan(event)
        assert plan.decision == "observe"
        assert plan.agent == "system"

    def test_github_pr_event(self):
        planner = Planner()
        event = Event(
            type="github_pr", source="github",
            payload={
                "action": "opened",
                "title": "Fix login bug",
                "body": "Fixes the login issue",
                "repository": {"full_name": "org/repo"},
                "sender": {"login": "alice"},
            },
        )
        plan = planner.plan(event)
        assert plan.decision == "act"
        assert plan.agent == "chat_agent"
        assert "GitHub pull request" in plan.task.payload["text"]
        assert plan.task.payload["context"]["repo"] == "org/repo"
        assert plan.task.payload["tools_allowed"] is False

    def test_unknown_event_type_ignored(self):
        planner = Planner()
        event = Event(type="system_tick", source="internal", payload={})
        plan = planner.plan(event)
        assert plan.decision == "observe"
