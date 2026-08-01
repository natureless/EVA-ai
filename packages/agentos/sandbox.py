"""SandboxManager — MVSC 沙箱管理器。

为高风险代码执行创建隔离环境:
- 临时目录隔离
- 环境变量最小化
- 网络访问控制
- 资源限制 (CPU/内存/时间)
- 输出大小限制

与现有 code_executor 的关系:
- code_executor 处理实际的 subprocess 执行
- SandboxManager 提供更高层的隔离策略
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("eva.agentos.sandbox")


class SandboxManager:
    """管理隔离执行环境。

    Usage::

        sm = SandboxManager()
        env = sm.create("task_123")
        try:
            result = sm.execute(env, ["python", "script.py"], code)
        finally:
            sm.cleanup(env)
    """

    def __init__(
        self,
        max_execution_sec: int = 30,
        max_memory_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,  # 1 MB
        allow_network: bool = False,
    ) -> None:
        self.max_execution_sec = max_execution_sec
        self.max_memory_mb = max_memory_mb
        self.max_output_bytes = max_output_bytes
        self.allow_network = allow_network
        self._active_sandboxes: dict[str, dict[str, Any]] = {}

    def create(self, task_id: str) -> dict[str, Any]:
        """创建隔离环境。

        Returns:
            {"task_id": str, "tmpdir": str, "created_at": float}
        """
        tmpdir = tempfile.mkdtemp(prefix=f"eva_sandbox_{task_id}_")
        env = {
            "task_id": task_id,
            "tmpdir": tmpdir,
            "created_at": __import__("time").time(),
            "files_created": [],
        }
        self._active_sandboxes[task_id] = env
        logger.debug("sandbox created: %s → %s", task_id, tmpdir)
        return env

    def build_env_vars(self) -> dict[str, str]:
        """构建最小化环境变量。"""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "PYTHONPATH": "",
            "LD_LIBRARY_PATH": "",
        }
        if not self.allow_network:
            env["HTTP_PROXY"] = ""
            env["HTTPS_PROXY"] = ""
            env["http_proxy"] = ""
            env["https_proxy"] = ""
            env["no_proxy"] = "*"
        return env

    def write_script(self, env: dict[str, Any], filename: str, code: str) -> Path:
        """在沙箱中写入脚本文件。"""
        path = Path(env["tmpdir"]) / filename
        path.write_text(code, encoding="utf-8")
        env["files_created"].append(str(path))
        return path

    def validate_code(self, code: str) -> tuple[bool, str]:
        """验证代码安全性。

        Returns:
            (is_safe, reason)
        """
        dangerous = [
            "rm -rf", "shutil.rmtree", "os.remove", "os.removedirs",
            "os.system", "subprocess.call", "subprocess.run",
            "exec(", "eval(", "compile(",
            "__import__", "importlib",
            "socket.", "requests.post", "httpx.post",
            "sudo", "chmod", "chown",
        ]

        code_lower = code.lower()
        for pattern in dangerous:
            if pattern in code_lower:
                return False, f"dangerous pattern detected: '{pattern}'"

        # Size check
        if len(code) > 100_000:
            return False, "code too large (>100KB)"

        return True, "safe"

    def cleanup(self, env: dict[str, Any]) -> None:
        """清理沙箱环境。"""
        import shutil

        tmpdir = env.get("tmpdir", "")
        if tmpdir and os.path.isdir(tmpdir):
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
                logger.debug("sandbox cleaned: %s", env.get("task_id"))
            except Exception:
                logger.warning("sandbox cleanup failed: %s", tmpdir)

        self._active_sandboxes.pop(env.get("task_id", ""), None)

    def cleanup_all(self) -> None:
        """清理所有活跃沙箱。"""
        for env in list(self._active_sandboxes.values()):
            self.cleanup(env)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "active_sandboxes": len(self._active_sandboxes),
            "max_execution_sec": self.max_execution_sec,
            "max_memory_mb": self.max_memory_mb,
        }
