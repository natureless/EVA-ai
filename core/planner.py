from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agents.base_agent import AgentTask
from event.event_schema import Event

logger = logging.getLogger("eva.planner")


# ── Explicit command prefixes ──────────────────────────────

_COMMANDS: list[tuple[tuple[str, ...], str, str]] = [
    # (prefixes, agent_name, task_kind)
    (("/search", "search:", "find:", "lookup:"), "search_agent", "search"),
    (("/code", "code:", "inspect:", "review:"), "coding_agent", "code"),
]

# Keyword sets for file-operation agents — only route when these
# appear AND the LLM supports the routing decision.
# Single-word tokens only (no phrases) to avoid word-boundary
# mismatches in _best_keyword_match substring check.
#
# NOTE: chat_agent now has tool-calling (list_directory, read_file, search_files),
# so keyword routing to search_agent is intentionally narrow — only explicit
# /search commands and strong search signals trigger the specialized agent.
_SEARCH_KEYWORDS = {
    "grep", "locate", "lookup",
    "搜索", "查找", "寻找",
    "搜", "找",
}

_CODE_KEYWORDS = {
    "inspect", "refactor", "patch",
    "审查", "重构", "查看代码", "代码",
    "review", "analyze", "debug",
}

_DOCS_KEYWORDS = {
    "summarize", "summary", "summarise",
    "摘要", "总结", "概括", "提取",
}


@dataclass
class Plan:
    """Represents a decision made by the planner about how to handle an event.

    Attributes:
        decision: Action decision ('act', 'observe', 'ignore')
        agent: Agent to route to
        task: Task specification for the agent
    """
    decision: str
    agent: str
    task: AgentTask


class Planner:
    """Plans how to handle incoming events.

    Routes events to appropriate agents based on event type and content.
    Uses explicit command prefixes → semantic embedding (when available)
    → keyword matching as fallback.  No LLM classification to avoid
    misrouting and latency.
    """

    def __init__(self, embedding_service: Any = None) -> None:
        self._embedding_service = embedding_service
        # Pre-compute agent description embeddings for semantic routing.
        # Lazily built on first use so the import doesn't block startup
        # when sentence-transformers is not installed.
        self._agent_embeddings: dict[str, Any] | None = None
        self._agent_descriptions: dict[str, str] = {
            "search_agent": "search find files directories grep locate lookup file system",
            "coding_agent": "inspect review analyze code refactor patch read file open file show file source code programming",
            "docs_agent": "summarize summary extract summarise documentation document",
            "chat_agent": "chat conversation talk discuss general question answer help",
        }

    def _build_embeddings(self) -> None:
        """Pre-compute agent description embeddings for semantic routing."""
        if self._embedding_service is None or self._agent_embeddings is not None:
            return
        try:
            self._agent_embeddings = {
                name: self._embedding_service.encode_single(desc)
                for name, desc in self._agent_descriptions.items()
            }
        except Exception:
            logger.exception("failed to build agent embeddings — semantic routing disabled")
            self._agent_embeddings = {}  # don't retry; fall back to keywords

    def _semantic_route(self, text: str) -> tuple[str, str] | None:
        """Route via cosine similarity against agent description embeddings.

        Returns (agent_name, task_kind) or None if embedding is unavailable
        or the best match is too weak (< 0.3 similarity).
        """
        self._build_embeddings()
        if not self._agent_embeddings or self._embedding_service is None:
            return None

        try:
            import numpy as np
            query_vec = self._embedding_service.encode_single(text)
            best_agent: str | None = None
            best_score = -1.0
            for name, desc_vec in self._agent_embeddings.items():
                # cosine similarity (both vectors are normalized by encode_single)
                score = float(np.dot(query_vec, desc_vec))
                if score > best_score:
                    best_score = score
                    best_agent = name

            if best_agent is None or best_score < 0.3:
                return None

            # Map agent name → task kind
            kind_map = {
                "search_agent": "search",
                "coding_agent": "code",
                "docs_agent": "summarize",
                "chat_agent": "chat",
            }
            return (best_agent, kind_map.get(best_agent, "chat"))
        except Exception:
            logger.warning("semantic routing failed — falling back to keywords", exc_info=True)
            return None

    def plan(self, event: Event) -> Plan:
        """Create a plan for handling the given event."""
        if event.type == "user_message":
            raw_text = str(event.payload.get("text", "")).strip()
            text = raw_text.lower()

            # 1. Explicit command prefixes — fast, deterministic
            for prefixes, agent_name, kind in _COMMANDS:
                if self._matches_command(text, *prefixes):
                    return Plan(
                        decision="act",
                        agent=agent_name,
                        task=AgentTask(kind=kind, payload={"text": raw_text}),
                    )

            # 2. Semantic embedding routing (when embedding_service is available)
            if self._embedding_service is not None:
                semantic_match = self._semantic_route(raw_text)
                if semantic_match is not None:
                    return Plan(
                        decision="act",
                        agent=semantic_match[0],
                        task=AgentTask(kind=semantic_match[1], payload={"text": raw_text}),
                    )

            # 3. Keyword-triggered file operations — pick best match (fallback)
            agent_match = _best_keyword_match(
                text,
                (_SEARCH_KEYWORDS, "search_agent", "search"),
                (_CODE_KEYWORDS, "coding_agent", "code"),
                (_DOCS_KEYWORDS, "docs_agent", "summarize"),
            )
            if agent_match is not None:
                return Plan(
                    decision="act",
                    agent=agent_match[0],
                    task=AgentTask(kind=agent_match[1], payload={"text": raw_text}),
                )

            # 4. Everything else → chat_agent
            return Plan(
                decision="act",
                agent="chat_agent",
                task=AgentTask(kind="chat", payload={"text": raw_text}),
            )

        if event.type == "reminder_trigger":
            return Plan(
                decision="act",
                agent="chat_agent",
                task=AgentTask(
                    kind="chat",
                    payload={
                        "text": event.payload.get("message", "[proactive] reminder"),
                        "tools_allowed": False,
                    },
                ),
            )

        if event.type in {"system_tick", "maintenance"}:
            return Plan(
                decision="observe",
                agent="system",
                task=AgentTask(kind="noop", payload={"reason": event.type}),
            )

        if event.type in {"github_push", "github_pr", "github_issue", "github_workflow"}:
            repo = event.payload.get("repository", {})
            repo_name = repo.get("full_name", "") if isinstance(repo, dict) else ""
            action = event.payload.get("action", "")
            sender = (event.payload.get("sender", {}) or {}).get("login", "")
            title = event.payload.get("title", "")
            body_text = event.payload.get("body", "") or ""
            # Build a chat prompt from the GitHub event
            kind_label = {
                "github_push": "push",
                "github_pr": "pull request",
                "github_issue": "issue",
                "github_workflow": "workflow run",
            }.get(event.type, "event")
            summary = (
                f"[GitHub {kind_label}] {sender} {action} on {repo_name}"
                + (f": {title}" if title else "")
                + (f"\n{body_text[:500]}" if body_text else "")
            )
            return Plan(
                decision="act",
                agent="chat_agent",
                task=AgentTask(
                    kind="chat",
                    payload={
                        "text": summary,
                        # GitHub bodies are untrusted external input and must
                        # never be allowed to drive file/network/code tools.
                        "tools_allowed": False,
                        "context": {
                            "github_event": event.type,
                            "repo": repo_name,
                            "action": action,
                            "sender": sender,
                        },
                    },
                ),
            )

        return Plan(
            decision="ignore",
            agent="none",
            task=AgentTask(kind="ignore", payload={"reason": event.type}),
        )

    @staticmethod
    def _matches_command(text: str, *prefixes: str) -> bool:
        return any(text.startswith(prefix) for prefix in prefixes)


def _best_keyword_match(
    text: str,
    *candidates: tuple[set[str], str, str],
) -> tuple[str, str] | None:
    """Return (agent_name, task_kind) with the most keyword matches in text.

    When text hits keywords from multiple agents (e.g. "find and review code"),
    the agent with the most matches wins rather than whichever was checked first.
    Returns None if no keywords match at all.
    """
    best: tuple[str, str] | None = None
    best_count = 0
    for keywords, agent_name, task_kind in candidates:
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best = (agent_name, task_kind)
    return best
