import logging
from typing import Any

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, MockLLM, load_system_prompt
from core.llm_helpers import build_context_text


logger = logging.getLogger(__name__)


class DocsAgent(BaseAgent):
    """Document processing agent with LLM-powered summarization.

    Accepts document text and produces structured summaries.
    Falls back to simple confirmation when no LLM API key.
    """
    name = "docs_agent"
    description = "Summarize and extract insights from documents"

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "summarize"

    def run(self, task: AgentTask) -> AgentResult:
        try:
            text = str(task.payload.get("text", "")).strip()
            if not text:
                return AgentResult(ok=True, agent=self.name,
                    content="[docs_agent] no document text provided",
                    summary="no input", meta={"source_length": 0})

            # ── LLM summary ────────────────────────────────
            context = task.payload.get("context")
            llm = get_llm()
            compact = " ".join(text.split())
            summary_text = self._llm_summarize(llm, compact, context)

            return AgentResult(ok=True, agent=self.name,
                content=summary_text, summary=summary_text[:120],
                meta={"source_length": len(text), "llm_provider": llm.provider})

        except Exception as e:
            logger.exception("docs_agent error: %s", e)
            return AgentResult(ok=False, agent=self.name,
                content=f"[docs_agent] error: {str(e)}",
                summary=str(e)[:120], meta={"error": type(e).__name__})

    def _llm_summarize(self, llm: Any, text: Any, context: Any = None) -> str:
        if isinstance(llm, MockLLM):
            preview = text[:160]
            return f"[docs_agent] document task accepted, preview: {preview}"

        ctx_text = build_context_text(context)
        system = load_system_prompt(
            "docs",
            persona_name="EVA",
            context_summary=ctx_text,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Document to summarize:\n{text[:5000]}"},
        ]
        return llm.chat(messages)  # type: ignore[no-any-return]
