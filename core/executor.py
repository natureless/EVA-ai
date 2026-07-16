"""Executor framework — sandboxed action execution with token gating and audit logging.

Each executor wraps agent execution with:
1. Token validation (via PolicyEngine.TokenManager)
2. Boundary checks (path whitelist, file size, timeout)
3. Audit logging (start → complete/denied → budget consumption)

Executors are NOT agents — they gate agent access to external resources.
Agents handle cognition; executors handle safety.
"""

import json
import logging
import subprocess
import time
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from memory.sqlite_store import SQLiteStore

logger = logging.getLogger("eva.executor")


# ── Audit Log ──────────────────────────────────────────────

@dataclass
class AuditEntry:
    eid: str
    executor_type: str
    action: str
    task_id: str
    token_id: str
    parameters: dict[str, Any]
    result_summary: str
    duration_ms: int
    status: str  # success | denied | error
    timestamp: str


class ExecutorAuditLog:
    """Immutable audit trail for all executor operations."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def record(
        self,
        *,
        executor_type: str,
        action: str,
        task_id: str,
        token_id: str = "",
        parameters: dict[str, Any] | None = None,
        result_summary: str = "",
        duration_ms: int = 0,
        status: str = "success",
    ) -> str:
        eid = f"audit_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute(
            """INSERT INTO executor_audit
               (id, executor_type, action, task_id, token_id,
                parameters_json, result_summary, duration_ms, status, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, executor_type, action, task_id, token_id,
                json.dumps(parameters or {}, ensure_ascii=False),
                result_summary, duration_ms, status, now,
            ),
        )
        logger.debug("audit: %s %s %s → %s", executor_type, action, task_id, status)
        return eid

    def query(
        self,
        executor_type: str = "",
        limit: int = 50,
        status: str = "",
    ) -> list[dict]:
        if executor_type and status:
            rows = self.store.fetchall(
                """SELECT * FROM executor_audit
                   WHERE executor_type = ? AND status = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (executor_type, status, limit),
            )
        elif executor_type:
            rows = self.store.fetchall(
                """SELECT * FROM executor_audit
                   WHERE executor_type = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (executor_type, limit),
            )
        elif status:
            rows = self.store.fetchall(
                """SELECT * FROM executor_audit
                   WHERE status = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (status, limit),
            )
        else:
            rows = self.store.fetchall(
                "SELECT * FROM executor_audit ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        for r in rows:
            r["parameters"] = json.loads(r.get("parameters_json", "{}"))
        return [dict(r) for r in rows]

    def count_by_type(self) -> list[dict]:
        return self.store.fetchall(
            """SELECT executor_type, status, COUNT(*) as cnt
               FROM executor_audit GROUP BY executor_type, status
               ORDER BY executor_type, status""",
        )


# ── Executor Decision ──────────────────────────────────────

@dataclass
class ExecutorDecision:
    allowed: bool
    reason: str = ""
    token_id: str = ""


# ── Base Executor ──────────────────────────────────────────

class BaseExecutor(ABC):
    name: str = "base"
    description: str = "abstract executor"

    def __init__(self, audit_log: ExecutorAuditLog, config: dict | None = None) -> None:
        self.audit_log = audit_log
        self.config = config or {}

    def validate_token(self, token_manager, token_id: str) -> ExecutorDecision:
        if token_manager is None:
            return ExecutorDecision(allowed=False, reason="no token manager available")
        if not token_id:
            return ExecutorDecision(allowed=False, reason="token_id required")
        decision = token_manager.validate(token_id)
        if decision.verdict.value == "deny":
            return ExecutorDecision(allowed=False, reason=f"token invalid: {decision.reason}")
        token_manager.consume_budget(token_id, 1)
        return ExecutorDecision(allowed=True, reason="token valid", token_id=token_id)

    @abstractmethod
    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        """Subclass-defined boundary checks."""
        ...

    def execute(
        self,
        action: str,
        params: dict[str, Any],
        *,
        task_id: str = "",
        token_id: str = "",
        token_manager=None,
    ) -> dict[str, Any]:
        """Template method: validate → boundary-check → execute → audit."""
        start = time.perf_counter()

        # 1. Token check
        if token_manager:
            tok = self.validate_token(token_manager, token_id)
            if not tok.allowed:
                self.audit_log.record(
                    executor_type=self.name, action=action, task_id=task_id,
                    token_id=token_id, parameters=params,
                    result_summary=tok.reason, status="denied",
                )
                return {"ok": False, "error": tok.reason, "status": "denied"}

        # 2. Boundary check
        boundary = self.check_boundaries(params)
        if not boundary.allowed:
            self.audit_log.record(
                executor_type=self.name, action=action, task_id=task_id,
                token_id=token_id, parameters=params,
                result_summary=boundary.reason, status="denied",
            )
            return {"ok": False, "error": boundary.reason, "status": "denied"}

        # 3. Execute
        try:
            result = self._run(action, params)
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.audit_log.record(
                executor_type=self.name, action=action, task_id=task_id,
                token_id=token_id, parameters=params,
                result_summary=result.get("summary", "")[:200],
                duration_ms=duration_ms, status="success",
            )
            result["status"] = "success"
            result["duration_ms"] = duration_ms
            return result
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception("executor %s action=%s error: %s", self.name, action, e)
            self.audit_log.record(
                executor_type=self.name, action=action, task_id=task_id,
                token_id=token_id, parameters=params,
                result_summary=str(e)[:200], duration_ms=duration_ms, status="error",
            )
            return {"ok": False, "error": str(e), "status": "error"}

    @abstractmethod
    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        ...


# ── File Executor ──────────────────────────────────────────

DEFAULT_FILE_ALLOWED = [
    str(Path.cwd()),          # project root
    str(Path.home() / "Documents"),
    str(Path.home() / "Downloads"),
    "/tmp/eva",
    "/data/eva",
]
DEFAULT_MAX_FILE_SIZE = 1024 * 1024  # 1 MB


class FileExecutor(BaseExecutor):
    name = "file"
    description = "Read/write/list/delete files within allowed paths"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("file", {})
        raw_paths = cfg.get("allowed_paths", DEFAULT_FILE_ALLOWED)
        self.allowed_paths = [Path(p) for p in raw_paths]
        self.max_file_size = cfg.get("limits", {}).get("max_file_size_mb", 1) * 1024 * 1024
        self.max_file_size = self.max_file_size or DEFAULT_MAX_FILE_SIZE

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        path_str = params.get("path", "")
        if not path_str:
            return ExecutorDecision(allowed=False, reason="path is required")
        target = Path(path_str).resolve()

        # path traversal check
        allowed = any(
            str(target).startswith(str(allowed_path.resolve()))
            for allowed_path in self.allowed_paths
        )
        if not allowed:
            return ExecutorDecision(
                allowed=False,
                reason=f"path {target} not in allowed paths",
            )

        action = params.get("action", params.get("kind", "read"))
        # search/inspect are boundary-only actions (agent does the I/O)
        if action in ("search", "inspect"):
            return ExecutorDecision(allowed=True, reason="boundary check passed")

        # file size check (for read)
        if action == "read" and target.is_file():
            size = target.stat().st_size
            if size > self.max_file_size:
                return ExecutorDecision(
                    allowed=False,
                    reason=f"file size {size} exceeds max {self.max_file_size}",
                )

        # delete requires confirmation
        if action == "delete" and not params.get("confirmed", False):
            return ExecutorDecision(
                allowed=False,
                reason="delete requires confirmation (confirmed=true)",
            )

        return ExecutorDecision(allowed=True, reason="boundary check passed")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"]).resolve()

        if action == "read":
            content = path.read_text(encoding="utf-8", errors="replace")
            return {
                "ok": True,
                "content": content,
                "summary": f"read {len(content)} bytes from {path.name}",
                "size": len(content),
                "path": str(path),
            }

        elif action == "write":
            content = str(params.get("content", ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {
                "ok": True,
                "summary": f"wrote {len(content)} bytes to {path.name}",
                "path": str(path),
            }

        elif action == "list":
            if not path.exists():
                return {"ok": False, "error": "path not found", "path": str(path)}
            entries = []
            for entry in sorted(path.iterdir()):
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            return {
                "ok": True,
                "entries": entries,
                "summary": f"listed {len(entries)} entries in {path.name}",
            }

        elif action == "delete":
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                import shutil
                shutil.rmtree(path)
            return {
                "ok": True,
                "summary": f"deleted {path.name}",
                "path": str(path),
            }

        return {"ok": False, "error": f"unknown action: {action}"}


# ── Code Executor ──────────────────────────────────────────

DEFAULT_CODE_TIMEOUT = 30     # seconds
DEFAULT_CODE_MAX_OUTPUT = 100 * 1024  # 100 KB


class CodeExecutor(BaseExecutor):
    name = "code"
    description = "Execute sandboxed scripts in a temp directory"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("code", {})
        limits = cfg.get("limits", {})
        self.timeout = limits.get("timeout", DEFAULT_CODE_TIMEOUT) if isinstance(limits.get("timeout"), int) else DEFAULT_CODE_TIMEOUT
        self.max_output = limits.get("max_output_size_mb", 0.1) * 1024 * 1024
        self.max_output = int(self.max_output) if self.max_output > 0 else DEFAULT_CODE_MAX_OUTPUT
        self.allowed_languages = {"python", "bash"}

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        language = params.get("language", "python")
        if language not in self.allowed_languages:
            return ExecutorDecision(
                allowed=False,
                reason=f"language '{language}' not allowed (allowed: {sorted(self.allowed_languages)})",
            )
        code = params.get("code", "")
        if not code.strip():
            return ExecutorDecision(allowed=False, reason="code is required")
        # block dangerous patterns
        dangerous = ["rm -rf /", "del /f", "shutdown", "reboot", "format ", "sudo "]
        if language == "bash" and any(d in code.lower() for d in dangerous):
            return ExecutorDecision(allowed=False, reason="dangerous command detected")
        return ExecutorDecision(allowed=True, reason="boundary check passed")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action != "execute":
            return {"ok": False, "error": f"unknown action: {action}"}

        language = params.get("language", "python")
        code = params.get("code", "")

        with tempfile.TemporaryDirectory(prefix="eva_code_") as tmpdir:
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
                    timeout=self.timeout,
                    cwd=tmpdir,
                    env={
                        "PATH": os.environ.get("PATH", "/usr/bin"),
                        "HOME": tmpdir,
                        "TMPDIR": tmpdir,
                    },
                )
                stdout = proc.stdout[:self.max_output] if proc.stdout else ""
                stderr = proc.stderr[:self.max_output] if proc.stderr else ""
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
                    "error": f"execution timed out after {self.timeout}s",
                    "stdout": "",
                    "stderr": "",
                    "returncode": -1,
                    "summary": f"timeout ({self.timeout}s)",
                }


# ── Skeleton Executors (v0.2+) ────────────────────────────

class BrowserExecutor(BaseExecutor):
    name = "browser"
    description = "Web browsing and form interaction (v0.2+)"

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        return ExecutorDecision(allowed=False, reason="browser executor not implemented in v0.1")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "browser executor not implemented in v0.1"}


class APIExecutor(BaseExecutor):
    name = "api"
    description = "External API calls and webhooks (v0.2+)"

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        return ExecutorDecision(allowed=False, reason="api executor not implemented in v0.1")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "api executor not implemented in v0.1"}


class CommsExecutor(BaseExecutor):
    name = "comms"
    description = "Messaging, email, notifications (v0.2+)"

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        return ExecutorDecision(allowed=False, reason="comms executor not implemented in v0.1")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "comms executor not implemented in v0.1"}
