from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, MockLLM, load_system_prompt
from core.llm_helpers import build_context_text
from core.chat_mode import configure_chat_llm


logger = logging.getLogger(__name__)


class CodingAgent(BaseAgent):
    """Code analysis agent with LLM-powered insight generation.

    Parses code files for structure (lines, classes, functions, preview),
    then uses LLM to produce an intelligent summary with architectural
    observations and improvement suggestions.
    """
    name = "coding_agent"
    description = "Analyze code files and generate intelligent summaries"

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or Path.cwd()).resolve()

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "code"

    def run(self, task: AgentTask) -> AgentResult:
        try:
            raw = str(task.payload.get("path") or task.payload.get("text") or "").strip()
            if not raw:
                return AgentResult(ok=True, agent=self.name,
                    content="[coding_agent] no input provided",
                    summary="no input", meta={"lines": 0})

            path = self._extract_path(raw)
            if path:
                resolved = (self.base_dir / path).resolve()
                if resolved.exists():
                    analysis, meta = self._analyze_file(resolved)
                else:
                    content = f"[coding_agent] file not found: {resolved}"
                    return AgentResult(ok=False, agent=self.name,
                        content=content, summary=content[:120],
                        meta={"path": str(resolved), "lines": 0})
            else:
                analysis, meta = self._analyze_text(raw)

            # ── LLM summary ────────────────────────────────
            context = task.payload.get("context")
            llm = configure_chat_llm(get_llm(), task.payload.get("mode", "normal"))
            content = self._llm_summarize(llm, analysis, meta, context)

            return AgentResult(ok=True, agent=self.name,
                content=content, summary=content[:120],
                meta={**meta, "llm_provider": llm.provider, "mode_info": llm.mode_info})

        except Exception as e:
            logger.exception("coding_agent error: %s", e)
            return AgentResult(ok=False, agent=self.name,
                content=f"[coding_agent] error: {str(e)}",
                summary=str(e)[:120], meta={"error": type(e).__name__})

    def _llm_summarize(self, llm: Any, analysis: Any, meta: Any, context: Any = None) -> str:
        if isinstance(getattr(llm, "inner", llm), MockLLM):
            path_hint = f" {meta.get('path', '')}" if meta.get("path") else ""
            return f"[coding_agent]{path_hint}\n{analysis}"

        ctx_text = build_context_text(context)
        system = load_system_prompt(
            "coding",
            persona_name="EVA",
            context_summary=ctx_text,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"File: {meta.get('path', 'inline code')}\n"
                f"Lines: {meta.get('lines', 0)} (non-empty: {meta.get('non_empty', 0)})\n"
                f"Classes: {meta.get('classes', [])}\n"
                f"Functions: {meta.get('functions', [])}\n\n"
                f"Preview (first 15 lines):\n{analysis[:3000]}"
            )},
        ]
        return llm.chat(messages)  # type: ignore[no-any-return]

    def _extract_path(self, raw: str) -> Path | None:
        lowered = raw.lower()
        for prefix in ("/code", "code:", "inspect:", "review:"):
            if lowered.startswith(prefix):
                candidate: str = raw[len(prefix):].strip()
                if candidate:
                    return Path(candidate)
        path = Path(raw)
        return path if path.suffix else None

    def _analyze_file(self, path: Path) -> tuple[str, dict[str, Any]]:
        max_size = 256 * 1024
        try:
            if path.stat().st_size > max_size:
                content = f"file too large ({path.stat().st_size} bytes)"
                return content, {"path": str(path), "lines": 0, "size": path.stat().st_size}
        except OSError as e:
            return f"unable to read: {e}", {"path": str(path), "lines": 0}

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            return f"unable to read: {e}", {"path": str(path), "lines": 0}

        analysis, meta = self._analyze_text(text)
        meta["path"] = str(path)
        meta["size"] = len(text.encode("utf-8", errors="ignore"))
        return analysis, meta

    def _analyze_text(self, text: str) -> tuple[str, dict[str, Any]]:
        lines = text.splitlines()
        total, non_empty = len(lines), sum(1 for line in lines if line.strip())
        classes, functions = [], []
        for line in lines:
            m = re.match(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                classes.append(m.group(1))
            m = re.match(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                functions.append(m.group(1))

        preview = "\n".join(lines[:15])
        summary = (
            f"lines={total}, non_empty={non_empty}, "
            f"classes={len(classes)}, functions={len(functions)}\n"
            f"preview:\n{preview}"
        )
        return summary, {"lines": total, "non_empty": non_empty,
                         "classes": classes[:10], "functions": functions[:10]}
