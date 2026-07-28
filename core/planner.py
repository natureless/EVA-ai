from dataclasses import dataclass

from agents.base_agent import AgentTask
from event.event_schema import Event


# ── Explicit command prefixes ──────────────────────────────

_COMMANDS: list[tuple[tuple[str, ...], str, str]] = [
    # (prefixes, agent_name, task_kind)
    (("/search", "search:", "find:", "lookup:"), "search_agent", "search"),
    (("/code", "code:", "inspect:", "review:"), "coding_agent", "code"),
]

# Keyword sets for file-operation agents — only route when these
# appear AND the LLM supports the routing decision.
_SEARCH_KEYWORDS = {
    "find", "search", "grep", "file", "dir", "path",
    "folder", "locate", "lookup", "where is",
    "搜索", "查找", "寻找", "文件",
}

_CODE_KEYWORDS = {
    "inspect", "review code", "analyze code", "check file",
    "read file", "open file", "show file", "refactor", "patch",
    "审查", "重构", "查看代码",
}

_DOCS_KEYWORDS = {
    "summarize", "summary", "extract from", "summarise",
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
    Uses explicit command prefixes and keyword matching — no LLM
    classification to avoid misrouting and latency.
    """

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

            # 2. Keyword-triggered file operations — pick best match
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

            # 3. Everything else → chat_agent
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
                    payload={"text": event.payload.get("message", "[proactive] reminder")},
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
            return Plan(
                decision="observe",
                agent="system",
                task=AgentTask(
                    kind="observe",
                    payload={
                        "reason": event.type,
                        "repo": repo_name,
                        "action": event.payload.get("action", ""),
                        "sender": (event.payload.get("sender", {}) or {}).get("login", ""),
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
