from __future__ import annotations

from typing import Any, Callable

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, load_system_prompt


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

    def run_stream(self, task: AgentTask, on_token: Callable[[str], None]) -> AgentResult:
        """Execute with per-token streaming via LLM chat_stream."""
        text = str(task.payload.get("text", "")).strip()
        context = task.payload.get("context")

        if not text:
            result = AgentResult(
                ok=True, agent=self.name,
                content="[chat_agent] empty input",
                summary="empty input", meta={},
            )
            on_token(result.content)
            return result

        messages = [{"role": "system", "content": self._build_system(context)}]
        messages.append({"role": "user", "content": text})

        llm = get_llm()
        full_reply = ""
        for token in llm.chat_stream(messages):
            full_reply += token
            on_token(token)

        return AgentResult(
            ok=True,
            agent=self.name,
            content=full_reply,
            summary=full_reply[:120],
            meta={
                "llm_provider": llm.provider,
                "has_context": bool(context),
                "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                "memories_count": len(context.get("memories", [])) if context else 0,
            },
        )

    def _build_system(self, context: dict[str, Any] | None) -> str:
        if context and context.get("persona"):
            p = context["persona"]
            persona_name = p.get("name", "EVA")
            persona_role = p.get("role_definition", "cognitive assistant")
            tone = p.get("tone_style", "precise, calm, concise")
            hard = "\n".join(f"- {c}" for c in p.get("hard_constraints", []))
        else:
            persona_name = "EVA"
            persona_role = "persistent cognitive assistant"
            tone = "precise, calm, concise"
            hard = "- do not fabricate memories\n- do not overclaim certainty"

        ctx_summary = context.get("context_summary", "") if context else ""

        return load_system_prompt(
            "chat",
            persona_name=persona_name,
            persona_role=persona_role,
            tone_style=tone,
            hard_constraints=hard,
            context_summary=ctx_summary,
        )
