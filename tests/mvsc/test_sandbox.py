"""Additional tests: SandboxManager + enhanced coverage for edge cases."""

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

from packages.agentos.sandbox import SandboxManager


# ═══════════════════════════════════════════════════════════
# SandboxManager tests
# ═══════════════════════════════════════════════════════════

class TestSandboxManager:
    def test_create_and_cleanup(self):
        sm = SandboxManager()
        env = sm.create("test_task")
        assert os.path.isdir(env["tmpdir"])
        assert sm.stats["active_sandboxes"] == 1

        sm.cleanup(env)
        assert not os.path.exists(env["tmpdir"])
        assert sm.stats["active_sandboxes"] == 0

    def test_write_and_read_script(self):
        sm = SandboxManager()
        env = sm.create("script_test")
        path = sm.write_script(env, "test.py", "print('hello')")
        assert path.exists()
        assert path.read_text() == "print('hello')"
        sm.cleanup(env)

    def test_validate_safe_code(self):
        sm = SandboxManager()
        safe, reason = sm.validate_code("print('hello world')")
        assert safe is True

    def test_validate_dangerous_code(self):
        sm = SandboxManager()
        safe, reason = sm.validate_code("import os; os.system('rm -rf /')")
        assert safe is False
        assert "dangerous pattern" in reason

    def test_validate_large_code(self):
        sm = SandboxManager()
        safe, reason = sm.validate_code("x" * 100_001)
        assert safe is False
        assert "too large" in reason

    def test_build_env_vars_minimal(self):
        sm = SandboxManager(allow_network=False)
        env = sm.build_env_vars()
        assert "PATH" in env
        assert env["PYTHONPATH"] == ""

    def test_build_env_vars_blocks_network(self):
        sm = SandboxManager(allow_network=False)
        env = sm.build_env_vars()
        assert env["no_proxy"] == "*"

    def test_cleanup_all(self):
        sm = SandboxManager()
        sm.create("a")
        sm.create("b")
        assert sm.stats["active_sandboxes"] == 2
        sm.cleanup_all()
        assert sm.stats["active_sandboxes"] == 0

    def test_multiple_dangerous_patterns(self):
        sm = SandboxManager()
        dangerous_codes = [
            "os.system('ls')",
            "subprocess.run(['ls'])",
            "eval('1+1')",
            "exec('x=1')",
            "shutil.rmtree('/')",
            "__import__('os')",
            "socket.socket()",
            "sudo rm -rf /",
            "chmod 777 /tmp",
        ]
        for code in dangerous_codes:
            safe, _ = sm.validate_code(code)
            assert safe is False, f"Should block: {code[:40]}"
