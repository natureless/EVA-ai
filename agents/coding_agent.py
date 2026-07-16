import logging
import re
from pathlib import Path

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from app.config import settings


logger = logging.getLogger("eva.coding_agent")


class CodingAgent(BaseAgent):
    """Inspect code files and generate summaries.
    
    Analyzes Python and text files to extract:
    - Line counts and structure
    - Class and function definitions
    - File previews
    
    Includes safeguards for large files and encoding issues.
    """
    name = "coding_agent"
    description = "Inspect a code file or snippet and return a compact summary"

    def can_handle(self, task: AgentTask) -> bool:
        """Coding agent handles code-type tasks."""
        return task.kind == "code"

    def run(self, task: AgentTask) -> AgentResult:
        """Summarize code from a file or text input.
        
        Args:
            task: Code task with path or text payload
            
        Returns:
            Result with code summary and metadata
        """
        try:
            raw = str(task.payload.get("path") or task.payload.get("text") or "").strip()
            if not raw:
                content = "[coding_agent] no input provided"
                return AgentResult(
                    ok=True,
                    agent=self.name,
                    content=content,
                    summary=content[:120],
                    meta={"lines": 0},
                )

            path = self._extract_path(raw)
            if path:
                resolved = (settings.base_dir / path).resolve()
                if resolved.exists():
                    content, meta = self._summarize_file(resolved)
                else:
                    content = f"[coding_agent] file not found: {resolved}"
                    logger.warning("file not found: %s", resolved)
                    meta = {"path": str(resolved), "lines": 0}
            else:
                content, meta = self._summarize_text(raw)

            return AgentResult(
                ok=True,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta=meta,
            )
        except Exception as e:
            logger.exception("coding_agent error: %s", e)
            content = f"[coding_agent] error: {str(e)}"
            return AgentResult(
                ok=False,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta={"error": type(e).__name__},
            )

    def _extract_path(self, raw: str) -> Path | None:
        """Extract file path from various input formats.
        
        Args:
            raw: Raw input string
            
        Returns:
            Path object or None if not a file reference
        """
        lowered = raw.lower()
        for prefix in ("/code", "code:", "inspect:", "review:"):
            if lowered.startswith(prefix):
                candidate = raw[len(prefix) :].strip()
                if candidate:
                    return Path(candidate)
        candidate = Path(raw)
        if candidate.suffix:
            return candidate
        return None

    def _summarize_file(self, path: Path) -> tuple[str, dict]:
        """Summarize a code file.
        
        Args:
            path: Path to the file
            
        Returns:
            Tuple of (summary_content, metadata)
        """
        max_size = 256 * 1024
        try:
            size = path.stat().st_size
            if size > max_size:
                content = f"[coding_agent] file too large to summarize: {path} ({size} bytes)"
                logger.debug("file too large: %s (%s bytes)", path, size)
                return content, {"path": str(path), "lines": 0, "size": size}
        except OSError as e:
            logger.debug("cannot stat file %s: %s", path, e)
            content = f"[coding_agent] unable to read file: {path}"
            return content, {"path": str(path), "lines": 0}

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.debug("cannot read file %s: %s", path, e)
            content = f"[coding_agent] unable to read file: {path}"
            return content, {"path": str(path), "lines": 0}

        summary, meta = self._summarize_text(text)
        meta["path"] = str(path)
        meta["size"] = len(text.encode("utf-8", errors="ignore"))
        content = f"[coding_agent] {path}\n{summary}"
        return content, meta

    def _summarize_text(self, text: str) -> tuple[str, dict]:
        """Extract summary from text content.
        
        Args:
            text: Code text to summarize
            
        Returns:
            Tuple of (summary_content, metadata)
        """
        lines = text.splitlines()
        total_lines = len(lines)
        non_empty = sum(1 for line in lines if line.strip())

        classes = []
        functions = []
        for line in lines:
            match_class = re.match(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match_class:
                classes.append(match_class.group(1))
            match_def = re.match(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match_def:
                functions.append(match_def.group(1))

        preview = "\n".join(lines[:15])
        summary = (
            f"lines={total_lines}, non_empty={non_empty}, "
            f"classes={len(classes)}, functions={len(functions)}\n"
            f"preview:\n{preview}"
        )

        meta = {
            "lines": total_lines,
            "non_empty": non_empty,
            "classes": classes[:10],
            "functions": functions[:10],
        }
        return summary, meta

