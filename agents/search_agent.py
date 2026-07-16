import logging
import os
from pathlib import Path

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from app.config import settings
from core.llm_adapter import get_llm, MockLLM, load_system_prompt
from core.llm_helpers import build_context_text


logger = logging.getLogger(__name__)


class SearchAgent(BaseAgent):
    """Search agent with LLM-powered result summarization.

    Searches local files for text matches, then uses LLM to produce
    an intelligent summary of findings. Falls back to raw match output
    when no LLM API key is configured.
    """
    name = "search_agent"
    description = "Search local files and summarize results intelligently"

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "search"

    def run(self, task: AgentTask) -> AgentResult:
        try:
            raw_query = str(task.payload.get("query") or task.payload.get("text") or "").strip()
            query = self._normalize_query(raw_query)
            if not query:
                return AgentResult(ok=True, agent=self.name,
                    content="[search_agent] no query provided",
                    summary="no query", meta={"query": "", "matches": 0})

            root = Path(task.payload.get("root") or settings.base_dir)
            if not root.exists():
                content = f"[search_agent] root path does not exist: {root}"
                return AgentResult(ok=False, agent=self.name,
                    content=content, summary=content[:120],
                    meta={"query": query, "error": "path_not_found"})

            matches, files_scanned, truncated = self._search(root, query)

            if not matches:
                content = f"[search_agent] no matches for '{query}' under {root}"
                return AgentResult(ok=True, agent=self.name,
                    content=content, summary=content[:120],
                    meta={"query": query, "root": str(root), "matches": 0,
                         "files_scanned": files_scanned})

            # ── LLM summary ────────────────────────────────
            context = task.payload.get("context")
            llm = get_llm()
            raw_output = "\n".join(matches[:15])
            summary = self._llm_summarize(llm, query, raw_output, files_scanned, truncated, context)

            return AgentResult(ok=True, agent=self.name,
                content=summary, summary=summary[:120],
                meta={"query": query, "root": str(root), "matches": len(matches),
                      "files_scanned": files_scanned, "truncated": truncated,
                      "llm_provider": llm.provider})

        except Exception as e:
            logger.exception("search_agent error: %s", e)
            return AgentResult(ok=False, agent=self.name,
                content=f"[search_agent] error: {str(e)}",
                summary=str(e)[:120], meta={"error": type(e).__name__})

    def _llm_summarize(self, llm, query, raw_output, files_scanned, truncated, context=None):
        if isinstance(llm, MockLLM):
            lines = "\n".join(raw_output.splitlines()[:20])
            return f"[search_agent] matches for '{query}' (scanned {files_scanned} files):\n{lines}"

        ctx_text = build_context_text(context)
        messages = [
            {"role": "system", "content": (
                "You are EVA's search agent. Summarize file search results concisely.\n"
                "Format: 1) What was found (2-3 sentences) 2) Key files and their relevance "
                "3) Suggested next action if applicable.\n"
                + ctx_text
            )},
            {"role": "user", "content": (
                f"Query: {query}\nFiles scanned: {files_scanned}\n"
                f"{'Results truncated to 20 matches.' if truncated else ''}\n"
                f"Raw matches:\n{raw_output[:3000]}"
            )},
        ]
        return llm.chat(messages)

    def _normalize_query(self, raw: str) -> str:
        lowered = raw.lower()
        for prefix in ("/search", "search:", "find:", "lookup:"):
            if lowered.startswith(prefix):
                return raw[len(prefix):].strip()
        return raw

    def _search(self, root: Path, query: str) -> tuple[list[str], int, bool]:
        max_files, max_matches, max_file_size = 2000, 20, 512 * 1024
        matches, files_scanned, truncated = [], 0, False
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "data", "logs", ".pytest_cache"}
        query_lower = query.lower()

        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [n for n in dirnames if n not in skip_dirs and not n.startswith(".")]
                for filename in filenames:
                    if files_scanned >= max_files:
                        return matches, files_scanned, True
                    path = Path(dirpath) / filename
                    try:
                        if path.stat().st_size > max_file_size: continue
                    except OSError: continue
                    try:
                        with path.open("r", encoding="utf-8", errors="ignore") as h:
                            for idx, line in enumerate(h, 1):
                                if query_lower in line.lower():
                                    snippet = line.strip()
                                    matches.append(f"{path.relative_to(root)}:{idx} {snippet}")
                                    if len(matches) >= max_matches:
                                        return matches, files_scanned + 1, True
                        files_scanned += 1
                    except OSError: continue
        except Exception:
            raise
        return matches, files_scanned, truncated
