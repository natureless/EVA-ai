import logging
import os
from pathlib import Path

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from app.config import settings


logger = logging.getLogger("eva.search_agent")


class SearchAgent(BaseAgent):
    """Search agent for finding text matches in local files.
    
    Implements full-text search with safeguards:
    - Maximum file size limit to prevent memory issues
    - Directory traversal depth and file count limits
    - Automatic handling of encoding errors
    """
    name = "search_agent"
    description = "Search local files for simple text matches"

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "search"

    def run(self, task: AgentTask) -> AgentResult:
        try:
            raw_query = str(task.payload.get("query") or task.payload.get("text") or "").strip()
            query = self._normalize_query(raw_query)
            if not query:
                content = "[search_agent] no query provided"
                return AgentResult(
                    ok=True,
                    agent=self.name,
                    content=content,
                    summary=content[:120],
                    meta={"query": "", "matches": 0},
                )

            root = Path(task.payload.get("root") or settings.base_dir)
            
            # Validate root exists and is accessible
            if not root.exists():
                content = f"[search_agent] root path does not exist: {root}"
                logger.warning("search root path not found: %s", root)
                return AgentResult(
                    ok=False,
                    agent=self.name,
                    content=content,
                    summary=content[:120],
                    meta={"query": query, "error": "path_not_found"},
                )
            
            matches, files_scanned, truncated = self._search(root, query)

            if not matches:
                content = f"[search_agent] no matches for '{query}' under {root}"
            else:
                lines = "\n".join(matches)
                content = f"[search_agent] matches for '{query}':\n{lines}"

            return AgentResult(
                ok=True,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta={
                    "query": query,
                    "root": str(root),
                    "matches": len(matches),
                    "files_scanned": files_scanned,
                    "truncated": truncated,
                },
            )
        except Exception as e:
            logger.exception("search_agent error: %s", e)
            content = f"[search_agent] error: {str(e)}"
            return AgentResult(
                ok=False,
                agent=self.name,
                content=content,
                summary=content[:120],
                meta={"error": type(e).__name__},
            )

    def _normalize_query(self, raw: str) -> str:
        lowered = raw.lower()
        for prefix in ("/search", "search:", "find:", "lookup:"):
            if lowered.startswith(prefix):
                return raw[len(prefix) :].strip()
        return raw

    def _search(self, root: Path, query: str) -> tuple[list[str], int, bool]:
        """Search for query string in files under root directory.
        
        Returns:
            Tuple of (matches, files_scanned, was_truncated)
        """
        max_files = 2000
        max_matches = 20
        max_file_size = 512 * 1024

        matches: list[str] = []
        files_scanned = 0
        truncated = False

        skip_dirs = {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            "data",
            "logs",
            ".pytest_cache",
        }

        query_lower = query.lower()

        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if name not in skip_dirs and not name.startswith(".")
                ]

                for filename in filenames:
                    if files_scanned >= max_files:
                        truncated = True
                        logger.debug("search truncated at max_files=%s", max_files)
                        return matches, files_scanned, truncated

                    path = Path(dirpath) / filename
                    try:
                        if path.stat().st_size > max_file_size:
                            continue
                    except OSError as e:
                        logger.debug("cannot stat file %s: %s", path, e)
                        continue

                    try:
                        with path.open("r", encoding="utf-8", errors="ignore") as handle:
                            for index, line in enumerate(handle, start=1):
                                if query_lower in line.lower():
                                    rel_path = path.relative_to(root)
                                    snippet = line.strip()
                                    matches.append(f"{rel_path}:{index} {snippet}")
                                    if len(matches) >= max_matches:
                                        truncated = True
                                        logger.debug("search truncated at max_matches=%s", max_matches)
                                        return matches, files_scanned + 1, truncated
                        files_scanned += 1
                    except OSError as e:
                        logger.debug("cannot read file %s: %s", path, e)
                        continue
        except Exception as e:
            logger.error("search traversal error: %s", e, exc_info=True)
            raise

        return matches, files_scanned, truncated

