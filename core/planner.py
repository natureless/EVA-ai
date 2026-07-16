from dataclasses import dataclass

from agents.base_agent import AgentTask, AgentResult
from event.event_schema import Event


# ── LLM intent → agent mapping ──────────────────────────────

_INTENT_AGENT_MAP: dict[str, str] = {
    "search": "search_agent",
    "code": "coding_agent",
    "summarize": "docs_agent",
    "chat": "chat_agent",
}


def _classify_with_llm(text: str) -> str | None:
    """Use LLM to classify user intent into an agent label.

    Returns an agent name string, or None if classification fails.
    Uses a lightweight 1-token prompt — fast and cheap.
    """
    try:
        from core.llm_adapter import get_llm, MockLLM
        llm = get_llm()
        if isinstance(llm, MockLLM):
            return None

        prompt = (
            "Classify this message into ONE word: chat, search, code, summarize.\n"
            "- chat: conversation, questions, greetings, general talk\n"
            "- search: find files, search content, look up information\n"
            "- code: analyze code, inspect files, review source\n"
            "- summarize: summarize documents, extract text insights\n"
            f"\nMessage: \"{text[:500]}\"\nLabel:"
        )
        label = llm.chat([{"role": "user", "content": prompt}]).strip().lower()
        return _INTENT_AGENT_MAP.get(label)
    except Exception:
        return None


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
    Supports command prefixes for specialized agents and keyword matching.
    """

    def plan(self, event: Event) -> Plan:
        """Create a plan for handling the given event.
        
        Args:
            event: The event to plan for
            
        Returns:
            A Plan object specifying decision, agent, and task
        """
        if event.type == "user_message":
            text = str(event.payload.get("text", "")).lower()
            raw_text = str(event.payload.get("text", "")).strip()

            # 1. Explicit command prefixes — fast, deterministic
            if self._matches_command(text, "/search", "search:", "find:", "lookup:"):
                return Plan(
                    decision="act",
                    agent="search_agent",
                    task=AgentTask(kind="search", payload={"text": raw_text}),
                )

            if self._matches_command(text, "/code", "code:", "inspect:", "review:"):
                return Plan(
                    decision="act",
                    agent="coding_agent",
                    task=AgentTask(kind="code", payload={"text": raw_text}),
                )

            # 2. LLM semantic classification (skipped for MockLLM)
            llm_agent = _classify_with_llm(raw_text)
            if llm_agent == "search_agent":
                return Plan(
                    decision="act",
                    agent="search_agent",
                    task=AgentTask(kind="search", payload={"text": raw_text}),
                )
            if llm_agent == "coding_agent":
                return Plan(
                    decision="act",
                    agent="coding_agent",
                    task=AgentTask(kind="code", payload={"text": raw_text}),
                )
            if llm_agent == "docs_agent":
                return Plan(
                    decision="act",
                    agent="docs_agent",
                    task=AgentTask(kind="summarize", payload={"text": event.payload.get("text", "")}),
                )

            # 3. Keyword hints (fast fallback when LLM unavailable)
            if any(keyword in text for keyword in ["summary", "docs", "document", "summarize"]):
                return Plan(
                    decision="act",
                    agent="docs_agent",
                    task=AgentTask(kind="summarize", payload={"text": event.payload.get("text", "")}),
                )

            return Plan(
                decision="act",
                agent="chat_agent",
                task=AgentTask(kind="chat", payload={"text": event.payload.get("text", "")}),
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

        return Plan(
            decision="ignore",
            agent="none",
            task=AgentTask(kind="ignore", payload={"reason": event.type}),
        )

    @staticmethod
    def _matches_command(text: str, *prefixes: str) -> bool:
        """Check if text starts with any of the given prefixes.
        
        Args:
            text: Text to check
            prefixes: Command prefixes to match
            
        Returns:
            True if text starts with any prefix
        """
        return any(text.startswith(prefix) for prefix in prefixes)

