"""Unit tests for BaseExecutor.execute() template method and validate_token."""

from unittest.mock import MagicMock

import pytest

from core.executor import BaseExecutor, ExecutorDecision, ExecutorAuditLog


class StubExecutor(BaseExecutor):
    """Minimal concrete executor for testing the template method."""
    name = "stub"
    description = "test executor"

    def check_boundaries(self, params):
        return ExecutorDecision(allowed=True, reason="ok")

    def _run(self, action, params):
        return {"ok": True, "summary": "stub executed", "data": params}


class TestBaseExecutor:
    def test_validate_token_no_manager(self):
        exe = StubExecutor(MagicMock(spec=ExecutorAuditLog))
        dec = exe.validate_token(None, "tok-1")
        assert dec.allowed is False
        assert "no token manager" in dec.reason

    def test_validate_token_empty_id(self):
        exe = StubExecutor(MagicMock(spec=ExecutorAuditLog))
        tm = MagicMock()
        dec = exe.validate_token(tm, "")
        assert dec.allowed is False
        assert "token_id required" in dec.reason

    def test_validate_token_denied(self):
        exe = StubExecutor(MagicMock(spec=ExecutorAuditLog))
        tm = MagicMock()
        tm.validate.return_value = MagicMock(verdict=MagicMock(value="deny"), reason="expired")
        dec = exe.validate_token(tm, "tok-1")
        assert dec.allowed is False

    def test_validate_token_allowed(self):
        exe = StubExecutor(MagicMock(spec=ExecutorAuditLog))
        tm = MagicMock()
        tm.validate.return_value = MagicMock(verdict=MagicMock(value="allow"), reason="ok")
        dec = exe.validate_token(tm, "tok-1")
        assert dec.allowed is True
        tm.consume_budget.assert_called_once_with("tok-1", 1)

    def test_execute_success_path(self):
        audit = MagicMock(spec=ExecutorAuditLog)
        exe = StubExecutor(audit)
        result = exe.execute("test_action", {"key": "val"}, task_id="task-1")
        assert result["ok"] is True
        assert result["summary"] == "stub executed"
        # Audit logged success
        audit.record.assert_called()

    def test_execute_token_denied(self):
        audit = MagicMock(spec=ExecutorAuditLog)
        exe = StubExecutor(audit)
        tm = MagicMock()
        tm.validate.return_value = MagicMock(verdict=MagicMock(value="deny"), reason="budget exhausted")
        result = exe.execute("test_action", {}, token_manager=tm, token_id="tok-1")
        assert result["ok"] is False
        assert result["status"] == "denied"
        audit.record.assert_called()

    def test_execute_boundary_denied(self):
        audit = MagicMock(spec=ExecutorAuditLog)

        class RestrictedExecutor(StubExecutor):
            def check_boundaries(self, params):
                return ExecutorDecision(allowed=False, reason="path not allowed")

        exe = RestrictedExecutor(audit)
        result = exe.execute("test_action", {"path": "/etc/passwd"})
        assert result["ok"] is False
        assert result["status"] == "denied"

    def test_execute_no_token_manager_skips_validation(self):
        """When token_manager is None, skip token validation entirely."""
        audit = MagicMock(spec=ExecutorAuditLog)
        exe = StubExecutor(audit)
        result = exe.execute("test_action", {}, token_manager=None)
        assert result["ok"] is True

    def test_execute_run_raises_logs_error(self):
        """When _run raises, the exception is caught, audited as error, and returned."""
        audit = MagicMock(spec=ExecutorAuditLog)

        class FailingExecutor(StubExecutor):
            def _run(self, action, params):
                raise RuntimeError("execution failed")

        exe = FailingExecutor(audit)
        result = exe.execute("test_action", {}, task_id="task-err")
        assert result["ok"] is False
        assert result["status"] == "error"
        assert "execution failed" in result["error"]
        # Audit should have been called with status="error"
        audit.record.assert_called()
        # Last call should be error audit
        last_call_kwargs = audit.record.call_args.kwargs
        assert last_call_kwargs["status"] == "error"

    def test_execute_passes_through_result(self):
        """The result from _run is returned with ok=True when no errors."""
        audit = MagicMock(spec=ExecutorAuditLog)

        class RichExecutor(StubExecutor):
            def _run(self, action, params):
                return {"ok": True, "summary": "rich result", "extra_field": 42}

        exe = RichExecutor(audit)
        result = exe.execute("test_action", {"input": "data"})
        assert result["ok"] is True
        assert result["extra_field"] == 42
