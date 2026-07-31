"""Executor framework — sandboxed action execution with token gating and audit logging.

Each executor wraps agent execution with:
1. Token validation (via PolicyEngine.TokenManager)
2. Boundary checks (path whitelist, file size, timeout)
3. Audit logging (start → complete/denied → budget consumption)

Executors are NOT agents — they gate agent access to external resources.
Agents handle cognition; executors handle safety.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import time
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from pathlib import Path
from uuid import uuid4

from memory.storage_adapter import BaseStorageAdapter

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

    def __init__(self, store: BaseStorageAdapter) -> None:
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
    ) -> list[dict[str, Any]]:
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

    def count_by_type(self) -> list[dict[str, Any]]:
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

    def __init__(self, audit_log: ExecutorAuditLog, config: dict[str, Any] | None = None) -> None:
        self.audit_log = audit_log
        self.config = config or {}

    def validate_token(self, token_manager: Any, token_id: str) -> ExecutorDecision:
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
        token_manager: Any = None,
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
    str(Path.cwd().resolve()),
    str(Path.home() / "Documents"),
    str(Path.home() / "Downloads"),
    str(Path(tempfile.gettempdir()) / "eva"),
]
DEFAULT_MAX_FILE_SIZE = 1024 * 1024  # 1 MB


class FileExecutor(BaseExecutor):
    name = "file"
    description = "Read/write/list/delete files within allowed paths"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("file", {})
        raw_paths = cfg.get("allowed_paths", DEFAULT_FILE_ALLOWED)
        self.allowed_paths = [Path(p) for p in raw_paths]
        self.max_file_size = cfg.get("limits", {}).get("max_file_size_mb", 1) * 1024 * 1024
        self.max_file_size = self.max_file_size or DEFAULT_MAX_FILE_SIZE
        # forbidden path patterns from constitution / config
        raw_forbidden = cfg.get("forbidden_paths", [])
        self.forbidden_prefixes: list[str] = [
            p.rstrip("*") for p in raw_forbidden if isinstance(p, str)
        ]

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        path_str = params.get("path", "")
        if not path_str:
            return ExecutorDecision(allowed=False, reason="path is required")
        target = Path(path_str).resolve()

        # path traversal check — use is_relative_to for correct prefix semantics
        # (startswith would match /data/eva-secret against /data/eva)
        allowed = any(
            target == allowed_path.resolve()
            or target.is_relative_to(allowed_path.resolve())
            for allowed_path in self.allowed_paths
        )
        if not allowed:
            return ExecutorDecision(
                allowed=False,
                reason=f"path {target} not in allowed paths",
            )

        # forbidden prefix check — resolve forbidden paths too
        target_str = str(target)
        for prefix in self.forbidden_prefixes:
            forbidden_path = Path(prefix).resolve()
            if target == forbidden_path or target.is_relative_to(forbidden_path):
                return ExecutorDecision(
                    allowed=False,
                    reason=f"path {target} matches forbidden prefix {prefix}",
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


def _parse_timeout_sec(value: int | str) -> int:
    """Parse a timeout value that may be an int or a string like '60 seconds'."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        import re
        m = re.match(r"(\d+)", value)
        if m:
            return int(m.group(1))
    return DEFAULT_CODE_TIMEOUT


class CodeExecutor(BaseExecutor):
    name = "code"
    description = "Execute sandboxed scripts in a temp directory"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("code", {})
        limits = cfg.get("limits", {})
        self.timeout = _parse_timeout_sec(limits.get("timeout", DEFAULT_CODE_TIMEOUT))
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


# ── Browser Executor ─────────────────────────────────────────

DEFAULT_BROWSER_TIMEOUT = 15  # seconds
DEFAULT_BROWSER_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
DEFAULT_BROWSER_ALLOWED_SCHEMES = {"http", "https"}

# Private-use and loopback ranges blocked for outbound executors
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def _is_private_or_loopback(hostname: str) -> bool:
    """Return True if hostname resolves to a private/loopback address.

    Used by BrowserExecutor and APIExecutor to prevent SSRF against
    internal networks, regardless of domain whitelist configuration.
    """
    import socket

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not a raw IP — try DNS resolution
        try:
            addr = ipaddress.ip_address(socket.gethostbyname(hostname))
        except (socket.gaierror, ValueError):
            return False  # unresolvable; let the request fail naturally

    return any(addr in net for net in _BLOCKED_NETWORKS)


class BrowserExecutor(BaseExecutor):
    name = "browser"
    description = "HTTP GET requests with URL whitelist and size limits"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("browser", {})
        limits = cfg.get("limits", {})
        self.timeout = _parse_timeout_sec(limits.get("timeout", DEFAULT_BROWSER_TIMEOUT))
        raw_max = limits.get("max_page_size_mb", 5)
        self.max_size = int(raw_max * 1024 * 1024) if isinstance(raw_max, (int, float)) else DEFAULT_BROWSER_MAX_SIZE
        self.allowed_domains: list[str] = cfg.get("allowed_domains", [])

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        url = params.get("url", "")
        if not url:
            return ExecutorDecision(allowed=False, reason="url is required")

        parsed = urlparse(url) if "://" in url else None
        if not parsed or parsed.scheme not in DEFAULT_BROWSER_ALLOWED_SCHEMES:
            return ExecutorDecision(allowed=False, reason=f"only http/https allowed, got: {url[:80]}")

        hostname = parsed.hostname or ""
        if not hostname:
            return ExecutorDecision(allowed=False, reason="could not parse hostname from url")

        # Always block private/loopback — SSRF prevention
        if _is_private_or_loopback(hostname):
            return ExecutorDecision(
                allowed=False,
                reason=f"private/loopback address blocked: {hostname}",
            )

        if self.allowed_domains:
            if not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in self.allowed_domains
            ):
                return ExecutorDecision(
                    allowed=False,
                    reason=f"domain {hostname} not in whitelist",
                )

        return ExecutorDecision(allowed=True, reason="boundary check passed")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        if action != "fetch":
            return {"ok": False, "error": f"unknown action: {action}"}

        url = params["url"]
        try:
            resp = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": "EVA/0.1 (cognitive-agent)"},
            )
            content_type = resp.headers.get("content-type", "")
            is_text = "text/" in content_type or "application/json" in content_type or "xml" in content_type
            body = resp.text[:self.max_size] if is_text else f"[binary: {len(resp.content)} bytes, type={content_type}]"
            return {
                "ok": resp.is_success,
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": body,
                "size": len(resp.content),
                "summary": f"HTTP {resp.status_code}, {len(resp.content)} bytes from {url[:100]}",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "status_code": 0, "body": "", "summary": f"fetch failed: {e}"}


# ── API Executor ────────────────────────────────────────────

DEFAULT_API_TIMEOUT = 15
DEFAULT_API_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


class APIExecutor(BaseExecutor):
    name = "api"
    description = "HTTP JSON API calls with method/header/body support"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("api", {})
        limits = cfg.get("limits", {})
        self.timeout = _parse_timeout_sec(limits.get("timeout", DEFAULT_API_TIMEOUT))
        raw_max = limits.get("max_response_size_mb", 10)
        self.max_size = int(raw_max * 1024 * 1024) if isinstance(raw_max, (int, float)) else DEFAULT_API_MAX_SIZE
        self.allowed_domains: list[str] = cfg.get("allowed_domains", [])

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        url = params.get("url", "")
        if not url:
            return ExecutorDecision(allowed=False, reason="url is required")

        method = params.get("method", "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return ExecutorDecision(allowed=False, reason=f"method {method} not allowed")

        parsed = urlparse(url) if "://" in url else None
        if not parsed or parsed.scheme not in {"http", "https"}:
            return ExecutorDecision(allowed=False, reason="only http/https allowed")

        hostname = parsed.hostname or ""
        if not hostname:
            return ExecutorDecision(allowed=False, reason="could not parse hostname from url")

        # Always block private/loopback — SSRF prevention
        if _is_private_or_loopback(hostname):
            return ExecutorDecision(
                allowed=False,
                reason=f"private/loopback address blocked: {hostname}",
            )

        if self.allowed_domains:
            if not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in self.allowed_domains
            ):
                return ExecutorDecision(
                    allowed=False,
                    reason=f"domain {hostname} not in whitelist",
                )

        return ExecutorDecision(allowed=True, reason="boundary check passed")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        if action != "call":
            return {"ok": False, "error": f"unknown action: {action}"}

        url = params["url"]
        method = params.get("method", "GET").upper()
        headers = params.get("headers") or {}
        body = params.get("body")

        try:
            resp = httpx.request(
                method, url,
                headers=headers,
                json=body if body and method in ("POST", "PUT", "PATCH") else None,
                timeout=self.timeout,
                follow_redirects=False,
            )
            data = resp.text[:self.max_size]
            return {
                "ok": resp.is_success,
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": data,
                "size": len(resp.content),
                "summary": f"HTTP {method} {resp.status_code}, {len(resp.content)} bytes",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "status_code": 0, "body": "", "summary": f"api call failed: {e}"}


# ── Comms Executor ──────────────────────────────────────────

DEFAULT_COMMS_DIR = str(Path(tempfile.gettempdir()) / "eva" / "notifications")


class CommsExecutor(BaseExecutor):
    name = "comms"
    description = "Log messages, write notifications, and emit alerts"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("comms", {})
        self.output_dir = Path(cfg.get("output_dir", DEFAULT_COMMS_DIR))
        self.allowed_levels = {"debug", "info", "warning", "error", "critical"}
        self.allowed_actions = {"log", "notify", "alert"}

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        action = params.get("action", params.get("kind", "log"))
        if action not in self.allowed_actions:
            return ExecutorDecision(
                allowed=False,
                reason=f"action '{action}' not allowed (allowed: {sorted(self.allowed_actions)})",
            )

        level = str(params.get("level", "info")).lower()
        if level not in self.allowed_levels:
            return ExecutorDecision(
                allowed=False,
                reason=f"level '{level}' not allowed (allowed: {sorted(self.allowed_levels)})",
            )

        message = str(params.get("message", params.get("content", "")))
        if not message.strip():
            return ExecutorDecision(allowed=False, reason="message is required")

        max_len = 10_000
        if len(message) > max_len:
            return ExecutorDecision(
                allowed=False,
                reason=f"message exceeds max length ({max_len} chars)",
            )

        return ExecutorDecision(allowed=True, reason="boundary check passed")

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        message = str(params.get("message", params.get("content", "")))
        level = str(params.get("level", "info")).lower()
        title = str(params.get("title", params.get("subject", "EVA Notification")))

        results: dict[str, Any] = {"ok": True, "actions": []}

        if action in ("log", "alert"):
            log_func = getattr(logger, level, logger.info)
            log_func("comms: %s", message[:500])
            results["actions"].append("logged")
            results["logged"] = True

        if action in ("notify", "alert"):
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                filename = f"notify_{ts}_{uuid4().hex[:8]}.txt"
                filepath = self.output_dir / filename
                lines = [
                    f"Title: {title}",
                    f"Level: {level.upper()}",
                    f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
                    f"",
                    message,
                ]
                filepath.write_text("\n".join(lines), encoding="utf-8")
                results["actions"].append("written")
                results["path"] = str(filepath)
            except Exception as e:
                results["actions"].append("write_failed")
                results["write_error"] = str(e)

        results["summary"] = f"comms {action}: {message[:150]}"
        return results
