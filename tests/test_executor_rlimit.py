"""Unit tests for CodeExecutor rlimit preexec_fn and boundary checks."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from core.executor import CodeExecutor, ExecutorAuditLog
from memory.sqlite_store import SQLiteStore

# Windows doesn't have the resource module — inject a mock so that
# _build_preexec_fn's `import resource` succeeds during tests.
if "resource" not in sys.modules:
    _mock_resource = MagicMock()
    _mock_resource.RLIMIT_AS = 9
    _mock_resource.RLIMIT_CPU = 0
    _mock_resource.RLIMIT_NPROC = 7
    _mock_resource.setrlimit = MagicMock()
    sys.modules["resource"] = _mock_resource


class TestCodeExecutorRlimit:
    """Tests for _build_preexec_fn with mocked resource module."""

    @classmethod
    def setup_class(cls):
        cls.store = SQLiteStore.__new__(SQLiteStore)
        cls.audit = MagicMock(spec=ExecutorAuditLog)

    def _make_executor(self, **limits):
        config = {
            "executors": {
                "code": {
                    "limits": limits,
                }
            }
        }
        return CodeExecutor(self.audit, config=config)

    def test_preexec_fn_returns_callable(self):
        exe = self._make_executor()
        fn = exe._build_preexec_fn()
        assert callable(fn)

    def test_memory_limit_set(self):
        _mock_resource.setrlimit.reset_mock()
        exe = self._make_executor(memory_limit_mb=512, cpu_limit_percent=0)
        fn = exe._build_preexec_fn()
        fn()
        # RLIMIT_AS should be called with (512MB, 512MB)
        _mock_resource.setrlimit.assert_any_call(9, (512 * 1024 * 1024, 512 * 1024 * 1024))

    def test_cpu_limit_set(self):
        _mock_resource.setrlimit.reset_mock()
        exe = self._make_executor(memory_limit_mb=0, cpu_limit_percent=50)
        exe.timeout = 10
        fn = exe._build_preexec_fn()
        fn()
        # 50% of 10s = 5 CPU-seconds
        _mock_resource.setrlimit.assert_any_call(0, (5, 5))

    def test_process_limit_set(self):
        _mock_resource.setrlimit.reset_mock()
        exe = self._make_executor(max_processes=32)
        fn = exe._build_preexec_fn()
        fn()
        _mock_resource.setrlimit.assert_any_call(7, (32, 32))

    def test_zero_limits_skipped(self):
        """When memory_limit_mb=0 and cpu_limit_percent=0, those rlimits are skipped."""
        _mock_resource.setrlimit.reset_mock()
        exe = self._make_executor(memory_limit_mb=0, cpu_limit_percent=0, max_processes=16)
        fn = exe._build_preexec_fn()
        fn()
        # Only RLIMIT_NPROC should be called
        assert _mock_resource.setrlimit.call_count == 1
        _mock_resource.setrlimit.assert_called_with(7, (16, 16))

    def test_oserror_gracefully_handled(self):
        """When setrlimit raises OSError, it's silently ignored."""
        _mock_resource.setrlimit.reset_mock()
        _mock_resource.setrlimit.side_effect = OSError("not supported")
        exe = self._make_executor(memory_limit_mb=256, cpu_limit_percent=0, max_processes=16)
        fn = exe._build_preexec_fn()
        fn()  # should not raise
        # Both memory and nproc were attempted
        assert _mock_resource.setrlimit.call_count == 2
        _mock_resource.setrlimit.side_effect = None  # reset


class TestCodeExecutorBoundaries:
    """Boundary check tests that don't require I/O."""

    @classmethod
    def setup_class(cls):
        cls.audit = MagicMock(spec=ExecutorAuditLog)
        cls.exe = CodeExecutor(cls.audit, config={
            "executors": {"code": {"limits": {}}}
        })

    def test_empty_code_rejected(self):
        decision = self.exe.check_boundaries({"language": "python", "code": ""})
        assert decision.allowed is False
        assert "required" in decision.reason

    def test_whitespace_only_rejected(self):
        decision = self.exe.check_boundaries({"language": "python", "code": "   \n  "})
        assert decision.allowed is False

    def test_invalid_language_rejected(self):
        decision = self.exe.check_boundaries({"language": "ruby", "code": "puts 'hi'"})
        assert decision.allowed is False
        assert "ruby" in decision.reason

    def test_python_code_allowed(self):
        decision = self.exe.check_boundaries({"language": "python", "code": "print(1)"})
        assert decision.allowed is True

    def test_bash_code_allowed(self):
        decision = self.exe.check_boundaries({"language": "bash", "code": "echo hi"})
        assert decision.allowed is True
