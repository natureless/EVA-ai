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

            # 2. Keyword-triggered file operations — only for clear file intent
            if _text_contains(text, _SEARCH_KEYWORDS):
                return Plan(
                    decision="act",
                    agent="search_agent",
                    task=AgentTask(kind="search", payload={"text": raw_text}),
                )

            if _text_contains(text, _CODE_KEYWORDS):
                return Plan(
                    decision="act",
                    agent="coding_agent",
                    task=AgentTask(kind="code", payload={"text": raw_text}),
                )

            if _text_contains(text, _DOCS_KEYWORDS):
                return Plan(
                    decision="act",
                    agent="docs_agent",
                    task=AgentTask(kind="summarize", payload={"text": raw_text}),
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


def _text_contains(text: str, keywords: set[str]) -> bool:
    return any(kw in text for kw in keywords)
