import logging

from agents.base_agent import AgentResult, AgentTask, BaseAgent


logger = logging.getLogger("eva.docs_agent")


class DocsAgent(BaseAgent):
    """Handle document-oriented summarization and extraction tasks.
    
    Processes text documents to extract summaries and key information.
    """
    name = "docs_agent"
    description = "Handle document-oriented summarize/extract tasks"

    def can_handle(self, task: AgentTask) -> bool:
        """Docs agent handles summarize-type tasks."""
        return task.kind == "summarize"

    def run(self, task: AgentTask) -> AgentResult:
        """Process a document summarization task.
        
        Args:
            task: Summarization task with text payload
            
        Returns:
            Result with document summary and metadata
        """
        try:
            text = str(task.payload.get("text", "")).strip()
            compact = " ".join(text.split())

            if not compact:
                content = "[docs_agent] no document text provided"
            else:
                preview = compact[:160]
                content = f"[docs_agent] document task accepted, preview: {preview}"

            return AgentResult(
                ok=True,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta={"source_length": len(text)},
            )
        except Exception as e:
            logger.exception("docs_agent error: %s", e)
            content = f"[docs_agent] error: {str(e)}"
            return AgentResult(
                ok=False,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta={"error": type(e).__name__},
            )
