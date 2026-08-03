"""Tool registry — defines tools that EVA agents can call.

Each tool has:
- name: unique identifier
- description: what the tool does (shown to LLM)
- parameters: JSON Schema for arguments
- handler: async function that executes the tool

Tools are registered at bootstrap and injected into ChatAgent for
the tool-calling loop.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("eva.tools")


# ── Tool Definition ─────────────────────────────────────────

@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., dict[str, Any]]

    def to_openai_schema(self) -> dict[str, Any]:
        """OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Anthropic tool-use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ── Tool Registry ───────────────────────────────────────────

class ToolRegistry:
    """Registry of available tools for agent tool-calling."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        return [t.to_anthropic_schema() for t in self._tools.values()]


# ── Built-in Tool Handlers ──────────────────────────────────

# Patterns that indicate dangerous code (blocked from execution)
_DANGEROUS_PATTERNS = [
    # File system destruction
    r"\brm\s+-rf\b", r"\bshutil\.rmtree\b", r"\bos\.remove\b",
    r"\bos\.removedirs\b", r"\bos\.unlink\b",
    # Process / system manipulation
    r"\bos\.kill\b", r"\bos\.system\b", r"\bsubprocess\.(call|run|Popen)\b",
    r"\bexec\s*\(|\beval\s*\(|\bcompile\s*\(",
    r"\b__import__\s*\(|\bimportlib\b",
    # Network exfiltration
    r"\bsocket\.", r"\brequests\.post\b", r"\bhttpx\.post\b",
    r"\burllib\.request\b",
    # Privilege escalation
    r"\bsudo\b", r"\bchmod\s", r"\bchown\s",
]


def _check_dangerous_code(code: str) -> str:
    """Check code for dangerous patterns. Returns the matched pattern description or empty string."""
    import re
    code_lower = code.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, code_lower):
            return f"pattern '{pattern}' detected"
    return ""


def _resolve_workspace_path(
    raw_path: str,
    workspace_root: Path | None = None,
) -> tuple[Path | None, str]:
    """Resolve a tool path and keep it inside EVA's configured workspace."""
    root = (workspace_root or Path.cwd()).resolve()
    candidate = Path(raw_path or ".")
    if not candidate.is_absolute():
        candidate = root / candidate
    target = candidate.resolve()

    if target != root and not target.is_relative_to(root):
        return None, f"path outside EVA workspace is not allowed: {target}"
    return target, ""


def _ingest_document(
    path: str = "",
    category: str = "",
    tiered_memory: Any = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Ingest a local file into EVA's long-term memory.

    Reads the file, chunks it, and stores each chunk in S3 long-term memory
    with FTS5 indexing for later semantic search.
    """
    file_path, path_error = _resolve_workspace_path(path, workspace_root)
    if file_path is None:
        return {"ok": False, "error": path_error}
    if not file_path.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not file_path.is_file():
        return {"ok": False, "error": f"not a file: {path}"}

    # Supported extensions
    allowed = {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv", ".log", ".rst", ".ini", ".cfg", ".toml"}
    if file_path.suffix.lower() not in allowed:
        return {"ok": False, "error": f"unsupported file type: {file_path.suffix}. Supported: {sorted(allowed)}"}

    max_size = 2 * 1024 * 1024  # 2 MB
    try:
        if file_path.stat().st_size > max_size:
            return {"ok": False, "error": f"file too large: {file_path.stat().st_size} bytes (max {max_size})"}
    except OSError as e:
        return {"ok": False, "error": str(e)}

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"ok": False, "error": str(e)}

    if not text.strip():
        return {"ok": False, "error": "file is empty"}

    # Chunk the content (simple paragraph-based chunking)
    chunks = _chunk_text(text, max_chunk_size=2000, overlap=200)
    cat = category or file_path.suffix.lstrip(".")

    # Ingest through the canonical memory manager so routing, indexing, and
    # governance remain consistent with all other memory writes.
    ingested = 0
    for i, chunk in enumerate(chunks[:50]):  # max 50 chunks
        try:
            if _store_chunk(
                chunk,
                source=str(file_path),
                category=cat,
                chunk_index=i,
                tiered_memory=tiered_memory,
            ):
                ingested += 1
        except Exception as exc:
            logger.warning("document chunk ingest failed path=%s chunk=%d: %s", file_path, i, exc)

    return {
        "ok": True,
        "path": str(file_path),
        "size": len(text),
        "chunks": len(chunks),
        "ingested": ingested,
        "category": cat,
        "summary": f"Ingested {ingested}/{len(chunks)} chunks from {file_path.name} ({len(text)} chars) into long-term memory",
    }


def _chunk_text(text: str, max_chunk_size: int = 2000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks at paragraph boundaries."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chunk_size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            # If a single paragraph is too long, split it
            if len(para) > max_chunk_size:
                # Split at sentence boundaries
                sentences = __import__('re').split(r'(?<=[.!。！？\n])\s*', para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) <= max_chunk_size:
                        current = (current + " " + sent) if current else sent
                    else:
                        if current:
                            chunks.append(current)
                        current = sent
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def _store_chunk(
    content: str,
    source: str = "",
    category: str = "",
    chunk_index: int = 0,
    tiered_memory: Any = None,
) -> bool:
    """Store one document chunk through the canonical memory API."""
    if tiered_memory is None:
        return False
    result = tiered_memory.ingest(
        content,
        importance=0.85,
        source=source,
        category=category or "document",
        tags=["document", f"chunk:{chunk_index}"],
    )
    return "s3" in result


def _search_files(
    query: str = "",
    root: str = ".",
    file_pattern: str = "*",
    max_results: int = 10,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Search for text in files under a directory."""
    import fnmatch

    root_path, path_error = _resolve_workspace_path(root, workspace_root)
    if root_path is None:
        return {"ok": False, "error": path_error}
    if not root_path.exists():
        return {"ok": False, "error": f"path not found: {root}"}

    skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "data", "logs", ".pytest_cache"}
    max_files = 500
    max_file_size = 512 * 1024
    matches: list[dict[str, Any]] = []
    files_scanned = 0

    query_lower = query.lower()
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [n for n in dirnames if n not in skip_dirs and not n.startswith(".")]
        for filename in filenames:
            if files_scanned >= max_files:
                break
            if file_pattern != "*" and not fnmatch.fnmatch(filename, file_pattern):
                continue
            file_path = Path(dirpath) / filename
            try:
                if file_path.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, 1):
                        if query_lower in line.lower():
                            matches.append({
                                "file": str(file_path.relative_to(root_path)),
                                "line": idx,
                                "content": line.strip()[:200],
                            })
                            if len(matches) >= max_results:
                                return {
                                    "ok": True,
                                    "matches": matches,
                                    "total_matches": len(matches),
                                    "files_scanned": files_scanned + 1,
                                    "truncated": True,
                                    "summary": f"Found {len(matches)} matches in {files_scanned + 1} files",
                                }
                files_scanned += 1
            except OSError:
                continue

    return {
        "ok": True,
        "matches": matches,
        "total_matches": len(matches),
        "files_scanned": files_scanned,
        "truncated": False,
        "summary": f"Found {len(matches)} matches in {files_scanned} files",
    }


def _read_file(
    path: str = "",
    max_lines: int = 100,
    start_line: int = 1,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Read a file's contents."""
    file_path, path_error = _resolve_workspace_path(path, workspace_root)
    if file_path is None:
        return {"ok": False, "error": path_error}
    if not file_path.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not file_path.is_file():
        return {"ok": False, "error": f"not a file: {path}"}

    max_lines = max(1, min(int(max_lines), 500))
    start_line = max(1, int(start_line))
    max_size = 512 * 1024
    try:
        if file_path.stat().st_size > max_size:
            return {
                "ok": True,
                "content": f"[File too large: {file_path.stat().st_size} bytes. Showing first {max_lines} lines.]",
                "path": str(file_path),
                "size": file_path.stat().st_size,
                "summary": f"file too large ({file_path.stat().st_size} bytes)",
            }
    except OSError as e:
        return {"ok": False, "error": str(e)}

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        total_lines = len(lines)

        start_idx = max(0, start_line - 1)
        end_idx = min(start_idx + max_lines, total_lines)
        selected = lines[start_idx:end_idx]

        return {
            "ok": True,
            "content": "\n".join(selected),
            "path": str(file_path),
            "total_lines": total_lines,
            "shown_lines": len(selected),
            "start_line": start_idx + 1,
            "summary": f"Read lines {start_idx + 1}-{end_idx} of {total_lines} from {file_path.name}",
        }
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _list_directory(
    path: str = ".",
    max_entries: int = 50,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """List files and directories."""
    dir_path, path_error = _resolve_workspace_path(path, workspace_root)
    if dir_path is None:
        return {"ok": False, "error": path_error}
    if not dir_path.exists():
        return {"ok": False, "error": f"path not found: {path}"}
    if not dir_path.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}

    max_entries = max(1, min(int(max_entries), 200))
    entries: list[dict[str, Any]] = []
    try:
        for entry in sorted(dir_path.iterdir()):
            if len(entries) >= max_entries:
                break
            is_dir = entry.is_dir()
            entries.append({
                "name": entry.name,
                "type": "directory" if is_dir else "file",
                "size": entry.stat().st_size if not is_dir else 0,
            })
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "path": str(dir_path),
        "entries": entries,
        "total_entries": len(entries),
        "summary": f"Listed {len(entries)} entries in {dir_path.name}",
    }


def _run_code(
    code: str = "",
    language: str = "python",
) -> dict[str, Any]:
    """Execute a short script in a sandboxed temp directory."""
    import subprocess
    import tempfile

    if language not in ("python", "bash"):
        return {"ok": False, "error": f"language '{language}' not supported"}

    if not code.strip():
        return {"ok": False, "error": "no code provided"}

    # ── Safety: block dangerous patterns ──────────────────
    dangerous = _check_dangerous_code(code)
    if dangerous:
        return {"ok": False, "error": f"blocked for safety: {dangerous}"}

    timeout = 30
    max_output = 50 * 1024

    with tempfile.TemporaryDirectory(prefix="eva_tool_") as tmpdir:
        if language == "python":
            script_path = os.path.join(tmpdir, "script.py")
            cmd = ["python", script_path]
        else:
            script_path = os.path.join(tmpdir, "script.sh")
            cmd = ["bash", script_path]

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin"),
                    "HOME": tmpdir,
                    "TMPDIR": tmpdir,
                },
            )
            stdout = proc.stdout[:max_output] if proc.stdout else ""
            stderr = proc.stderr[:max_output] if proc.stderr else ""
            return {
                "ok": proc.returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": proc.returncode,
                "summary": f"exit {proc.returncode}, stdout={len(stdout)} stderr={len(stderr)}",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"execution timed out after {timeout}s",
                "stdout": "",
                "stderr": "",
                "summary": f"timeout ({timeout}s)",
            }


def _web_fetch(
    url: str = "",
    max_bytes: int = 100_000,
) -> dict[str, Any]:
    """Fetch a web page (HTTP GET)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    import httpx

    if not url:
        return {"ok": False, "error": "url is required"}

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"ok": False, "error": "only absolute http/https URLs are allowed"}

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if any((ip.is_private, ip.is_loopback, ip.is_link_local,
                    ip.is_reserved, ip.is_multicast, ip.is_unspecified)):
                return {"ok": False, "error": f"private or reserved address blocked: {address}"}
    except OSError as e:
        return {"ok": False, "error": f"hostname resolution failed: {e}"}

    max_bytes = max(1, min(int(max_bytes), 1_000_000))
    try:
        resp = httpx.get(
            url,
            timeout=15,
            follow_redirects=False,
            headers={"User-Agent": "EVA/0.1 (cognitive-agent)"},
        )
        content_type = resp.headers.get("content-type", "")
        is_text = "text/" in content_type or "application/json" in content_type or "xml" in content_type
        body = resp.text[:max_bytes] if is_text else f"[binary: {len(resp.content)} bytes, type={content_type}]"
        return {
            "ok": resp.is_success,
            "status_code": resp.status_code,
            "body": body,
            "size": len(resp.content),
            "summary": f"HTTP {resp.status_code}, {len(resp.content)} bytes from {url[:100]}",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "summary": f"fetch failed: {e}"}


def _read_memory(
    query: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Stub for reading EVA's memory. Wired at bootstrap with actual tiered_memory."""
    return {
        "ok": True,
        "memories": [],
        "summary": "Memory search not yet wired (use bootstrap injection)",
    }


# ── Built-in Tool Set ───────────────────────────────────────

def create_builtin_tools(
    tiered_memory: Any = None,
    executors: dict[str, Any] | None = None,
    *,
    enable_code: bool = False,
    enable_network: bool = False,
    workspace_root: Path | None = None,
) -> list[ToolDef]:
    """Create the standard tool set with optional runtime dependencies.

    Args:
        tiered_memory: TieredMemoryManager for memory_search tool
        executors: Dict of executors for boundary checks and audit logging.
        enable_code: Register host code execution tool (disabled by default).
        enable_network: Register outbound network tools (disabled by default).
        workspace_root: Root directory available to built-in file tools.
    """
    tools: list[ToolDef] = []
    file_executor = (executors or {}).get("file")
    code_executor = (executors or {}).get("code")
    browser_executor = (executors or {}).get("browser")
    workspace_root = (workspace_root or Path.cwd()).resolve()

    def search_files(**kwargs: Any) -> dict[str, Any]:
        return _search_files(**kwargs, workspace_root=workspace_root)

    def read_file(**kwargs: Any) -> dict[str, Any]:
        return _read_file(**kwargs, workspace_root=workspace_root)

    def list_directory(**kwargs: Any) -> dict[str, Any]:
        return _list_directory(**kwargs, workspace_root=workspace_root)

    def guarded_file_handler(handler: Callable[..., dict[str, Any]], action: str, path_arg: str):
        if file_executor is None:
            return handler

        def _guarded(**kwargs: Any) -> dict[str, Any]:
            params = dict(kwargs)
            params["path"] = kwargs.get(path_arg, ".")
            params["action"] = action
            decision = file_executor.check_boundaries(params)
            started = time.perf_counter()
            if not decision.allowed:
                file_executor.audit_log.record(
                    executor_type="file", action=action, task_id=f"tool_{action}",
                    parameters=params, result_summary=decision.reason, status="denied",
                )
                return {"ok": False, "error": decision.reason, "status": "denied"}
            result = handler(**kwargs)
            file_executor.audit_log.record(
                executor_type="file", action=action, task_id=f"tool_{action}",
                parameters=params, result_summary=str(result.get("summary", ""))[:200],
                duration_ms=int((time.perf_counter() - started) * 1000),
                status="success" if result.get("ok") else "error",
            )
            return result

        return _guarded

    # ── search_files ──
    tools.append(ToolDef(
        name="search_files",
        description="Search for text in files under a directory. Use this to find code, "
                    "documentation, or any text content in the project.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive substring match)",
                },
                "root": {
                    "type": "string",
                    "description": "Root directory to search in (default: current directory)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern for file names (e.g., '*.py', '*.md'). Default: '*'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10, max: 20)",
                },
            },
            "required": ["query"],
        },
        handler=guarded_file_handler(search_files, "search", "root"),
    ))

    # ── read_file ──
    tools.append(ToolDef(
        name="read_file",
        description="Read the contents of a file. Use this to inspect code, "
                    "configuration, or documentation files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: 100)",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, default: 1)",
                },
            },
            "required": ["path"],
        },
        handler=guarded_file_handler(read_file, "read", "path"),
    ))

    # ── list_directory ──
    tools.append(ToolDef(
        name="list_directory",
        description="List files and subdirectories in a directory. Use this to "
                    "explore project structure.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default: 50)",
                },
            },
            "required": [],
        },
        handler=guarded_file_handler(list_directory, "list", "path"),
    ))

    # ── run_code ──
    if enable_code:
        def _execute_code_tool(code: str = "", language: str = "python") -> dict[str, Any]:
            if code_executor is None:
                return _run_code(code=code, language=language)
            return code_executor.execute(
                "execute", {"code": code, "language": language},
                task_id="tool_run_code",
            )

        tools.append(ToolDef(
            name="run_code",
            description="Execute a short Python or Bash script in a sandboxed environment. "
                        "Use this for calculations, data processing, or quick automation.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to execute",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python", "bash"],
                        "description": "Programming language (default: python)",
                    },
                },
                "required": ["code"],
            },
            handler=_execute_code_tool,
        ))

    # ── web_fetch ──
    if enable_network:
        def _execute_web_tool(url: str = "", max_bytes: int = 100_000, **_: Any) -> dict[str, Any]:
            if browser_executor is None:
                return _web_fetch(url=url, max_bytes=max_bytes)
            return browser_executor.execute(
                "fetch", {"url": url, "max_bytes": max_bytes},
                task_id="tool_web_fetch",
            )

        tools.append(ToolDef(
            name="web_fetch",
            description="Fetch content from a URL (HTTP GET). Use this to retrieve "
                        "web pages, API responses, or online documentation.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch (must be http or https)",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read (default: 100000)",
                    },
                },
                "required": ["url"],
            },
            handler=_execute_web_tool,
        ))

    # ── search_memory ── (wired with tiered_memory at bootstrap)
    if tiered_memory is not None:
        def _memory_search(query: str = "", limit: int = 5) -> dict[str, Any]:
            try:
                results = tiered_memory.recall(query, tiers=[1, 2, 3])
                memories = []
                for r in results[:limit]:
                    memories.append({
                        "tier": r.get("tier", "?"),
                        "content": str(r.get("content", ""))[:300],
                        "source": r.get("source", ""),
                    })
                return {
                    "ok": True,
                    "memories": memories,
                    "total_found": len(results),
                    "summary": f"Found {len(results)} memories matching '{query}'",
                }
            except Exception as e:
                return {"ok": False, "error": str(e), "memories": []}

        tools.append(ToolDef(
            name="search_memory",
            description="Search EVA's memory (past conversations, tasks, knowledge). "
                        "Use this to recall previous context or user preferences.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for memory recall",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum memories to return (default: 5)",
                    },
                },
                "required": ["query"],
            },
            handler=_memory_search,
        ))

    # ── browser tool ── (explicit opt-in; shares the guarded fetch path)
    if enable_network:
        tools.append(ToolDef(
            name="browse_web",
            description="Navigate to a web page and extract its text content. "
                        "Use this to read articles, documentation, or any web page.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to (must be http or https)",
                    },
                    "wait_until": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "description": "When to consider navigation complete (default: domcontentloaded)",
                    },
                },
                "required": ["url"],
            },
            handler=_execute_web_tool,
        ))

    # ── document ingestion ── (added in v0.2)
    def _ingest_tool(path: str = "", category: str = "") -> dict[str, Any]:
        return _ingest_document(
            path,
            category,
            tiered_memory=tiered_memory,
            workspace_root=workspace_root,
        )

    tools.append(ToolDef(
        name="ingest_document",
        description="Read a local file and ingest its content into EVA's long-term memory. "
                    "Supports .txt, .md, .py, .json, .yaml, and .csv files. "
                    "The content is chunked and stored for later retrieval via search_memory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to ingest",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category tag (e.g., 'docs', 'code', 'notes')",
                },
            },
            "required": ["path"],
        },
        handler=guarded_file_handler(_ingest_tool, "read", "path"),
    ))

    return tools


# ── Tool Execution ──────────────────────────────────────────

def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    registry: ToolRegistry,
) -> dict[str, Any]:
    """Execute a tool by name and return the result.

    Returns {"ok": False, "error": "..."} if the tool is not found or fails.
    """
    tool = registry.get(tool_name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {tool_name}"}

    try:
        result = tool.handler(**tool_args)
        return result
    except TypeError as e:
        logger.warning("tool %s called with wrong args: %s — args=%s", tool_name, e, tool_args)
        return {"ok": False, "error": f"invalid arguments for {tool_name}: {e}"}
    except Exception as e:
        logger.exception("tool %s execution failed: %s", tool_name, e)
        return {"ok": False, "error": f"tool execution error: {e}"}


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Format a tool result for injection into the LLM conversation.

    Returns a compact text representation suitable for appending as
    a system/tool message.
    """
    if not result.get("ok"):
        return f"[Tool {tool_name} error] {result.get('error', 'unknown error')}"

    summary = result.get("summary", "")

    # search_files: list matches
    if tool_name == "search_files" and "matches" in result:
        lines = [f"[{tool_name}] {summary}"]
        for m in result["matches"][:10]:
            lines.append(f"  {m['file']}:{m['line']} — {m['content'][:120]}")
        return "\n".join(lines)

    # read_file: show content
    if tool_name == "read_file" and "content" in result:
        return f"[{tool_name}] {summary}\n\n{result['content']}"

    # list_directory: show entries
    if tool_name == "list_directory" and "entries" in result:
        lines = [f"[{tool_name}] {summary}"]
        for e in result["entries"]:
            icon = "📁" if e["type"] == "directory" else "📄"
            size_info = f" ({e['size']} bytes)" if e["type"] == "file" else ""
            lines.append(f"  {icon} {e['name']}{size_info}")
        return "\n".join(lines)

    # run_code: show output
    if tool_name == "run_code":
        out = [f"[{tool_name}] {summary}"]
        if result.get("stdout"):
            out.append(f"stdout:\n{result['stdout']}")
        if result.get("stderr"):
            out.append(f"stderr:\n{result['stderr']}")
        return "\n".join(out)

    # web_fetch: show body
    if tool_name == "web_fetch" and "body" in result:
        return f"[{tool_name}] {summary}\n\n{result['body'][:2000]}"

    # search_memory: list memories
    if tool_name == "search_memory" and "memories" in result:
        lines = [f"[{tool_name}] {summary}"]
        for m in result["memories"]:
            lines.append(f"  [{m.get('tier', '?')}] {m['content'][:200]}")
        return "\n".join(lines)

    # generic fallback
    return f"[{tool_name}] {summary}"
