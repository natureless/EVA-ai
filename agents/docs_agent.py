import logging

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, MockLLM


logger = logging.getLogger("eva.docs_agent")


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

    def _llm_summarize(self, llm, text, context=None):
        if isinstance(llm, MockLLM):
            preview = text[:160]
            return f"[docs_agent] document task accepted, preview: {preview}"

        ctx_text = ""
        if context and isinstance(context, dict):
            tasks = context.get("active_tasks", [])
            if tasks:
                ctx_text += "Active tasks: " + ", ".join(
                    t.get("name","") for t in tasks[:3] if t.get("name")) + "\n"

        messages = [
            {"role": "system", "content": (
                "You are EVA's document analysis agent. Summarize documents concisely.\n"
                "Format: 1) Main topic (1 sentence) 2) Key points (bullet list, max 5) "
                "3) Action items if any.\nKeep it under 250 words.\n" + ctx_text
            )},
            {"role": "user", "content": f"Document to summarize:\n{text[:5000]}"},
        ]
        return llm.chat(messages)
