from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, MockLLM


class ChatAgent(BaseAgent):
    """General conversational task handler with LLM and context awareness.

    Uses an LLM adapter (auto-detected from environment) when available,
    falling back to deterministic echo mode. Context from memory chains
    and world model is injected into the system prompt.
    """
    name = "chat_agent"
    description = "Handle general conversational tasks with LLM + context"

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "chat"

    def run(self, task: AgentTask) -> AgentResult:
        text = str(task.payload.get("text", "")).strip()
        context = task.payload.get("context")

        if not text:
            return AgentResult(
                ok=True, agent=self.name,
                content="[chat_agent] empty input",
                summary="empty input", meta={},
            )

        # ── build messages ──────────────────────────────────
        messages = [{"role": "system", "content": self._build_system(context)}]
        messages.append({"role": "user", "content": text})

        # ── get LLM ─────────────────────────────────────────
        llm = get_llm()
        reply = llm.chat(messages)

        return AgentResult(
            ok=True,
            agent=self.name,
            content=reply,
            summary=reply[:120],
            meta={
                "llm_provider": llm.provider,
                "has_context": bool(context),
                "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                "memories_count": len(context.get("memories", [])) if context else 0,
            },
        )

    def _build_system(self, context: dict | None) -> str:
        base = (
            "You are EVA, a persistent cognitive assistant with stable identity. "
            "Be precise, calm, and concise. Do not overclaim certainty. "
            "Acknowledge what you know and don't know."
        )

        if not context:
            return base

        parts = [base, ""]

        summary = context.get("context_summary", "")
        if summary:
            parts.append("Current context from memory:")
            parts.append(summary)

        active_tasks = context.get("active_tasks", [])
        if active_tasks:
            task_names = [t.get("name", "") for t in active_tasks if t.get("name")]
            if task_names:
                parts.append(f"Active tasks: " + ", ".join(task_names[:5]))

        memories = context.get("memories", [])
        if memories:
            parts.append("Relevant past interactions:")
            for i, m in enumerate(memories[:5], 1):
                parts.append(f"  {i}. {m.get('content', '')[:120]}")

        return "\n".join(parts)
