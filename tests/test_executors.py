"""Executor framework unit + integration tests."""

import json
import tempfile
import os
from pathlib import Path

from memory.sqlite_store import SQLiteStore
from core.executor import (
    ExecutorAuditLog,
    FileExecutor,
    CodeExecutor,
    BrowserExecutor,
    APIExecutor,
    CommsExecutor,
)
from core.policy_engine import TokenManager


# ── Audit Log ──────────────────────────────────────────────

class TestExecutorAuditLog:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_record_and_query(self):
        audit = ExecutorAuditLog(self.store)
        eid = audit.record(
            executor_type="file", action="read", task_id="task-1",
            parameters={"path": "/tmp/test.txt"}, result_summary="ok", status="success",
        )
        assert eid.startswith("audit_")
        items = audit.query(executor_type="file", limit=10)
        assert len(items) >= 1
        assert items[0]["executor_type"] == "file"
        assert items[0]["status"] == "success"

    def test_query_by_status(self):
        audit = ExecutorAuditLog(self.store)
        audit.record(executor_type="code", action="execute", task_id="t2",
                     result_summary="timeout", status="error")
        items = audit.query(status="error", limit=10)
        assert len(items) >= 1

    def test_count_by_type(self):
        audit = ExecutorAuditLog(self.store)
        counts = audit.count_by_type()
        assert isinstance(counts, list)


# ── File Executor ──────────────────────────────────────────

class TestFileExecutor:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()
        cls.audit = ExecutorAuditLog(cls.store)

        # create test file inside allowed path
        os.makedirs(os.path.join(cls.tmpdir, "workspace"), exist_ok=True)
        cls.test_file = os.path.join(cls.tmpdir, "workspace", "hello.txt")
        with open(cls.test_file, "w") as f:
            f.write("hello executor world")

    def test_read_allowed_file(self):
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        result = executor.execute("read", {"path": self.test_file})
        assert result["ok"]
        assert "hello executor" in result["content"]

    def test_path_not_allowed(self):
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        result = executor.execute("read", {"path": "/etc/shadow"})
        assert not result["ok"]
        assert result["status"] == "denied"

    def test_list_directory(self):
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        result = executor.execute("list", {"path": os.path.join(self.tmpdir, "workspace")})
        assert result["ok"]
        assert len(result["entries"]) >= 1

    def test_write_and_read(self):
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        target = os.path.join(self.tmpdir, "workspace", "new.txt")
        executor.execute("write", {"path": target, "content": "created by test"})
        result = executor.execute("read", {"path": target})
        assert result["ok"]
        assert result["content"] == "created by test"

    def test_token_required_when_manager_present(self):
        executor = FileExecutor(self.audit, config={
            "executors": {"file": {"allowed_paths": [self.tmpdir]}}
        })
        tm = TokenManager()
        result = executor.execute("read", {"path": self.test_file},
                                  token_manager=tm, token_id="nonexistent")
        assert not result["ok"]
        assert result["status"] == "denied"


# ── Code Executor ──────────────────────────────────────────

class TestCodeExecutor:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()
        cls.audit = ExecutorAuditLog(cls.store)

    def test_execute_python(self):
        executor = CodeExecutor(self.audit)
        result = executor.execute("execute", {"code": "print('hello')", "language": "python"})
        assert result["ok"]
        assert "hello" in result["stdout"]

    def test_execute_timeout(self):
        executor = CodeExecutor(self.audit)
        executor.timeout = 1
        result = executor.execute("execute", {
            "code": "import time; time.sleep(10)",
            "language": "python",
        })
        assert not result["ok"]
        assert "timed out" in result.get("error", "").lower()

    def test_dangerous_bash_blocked(self):
        executor = CodeExecutor(self.audit)
        boundary = executor.check_boundaries({
            "code": "sudo rm -rf /", "language": "bash",
        })
        assert not boundary.allowed

    def test_invalid_language(self):
        executor = CodeExecutor(self.audit)
        boundary = executor.check_boundaries({
            "code": "console.log('x')", "language": "javascript",
        })
        assert not boundary.allowed


# ── Skeleton Executors ─────────────────────────────────────

class TestSkeletonExecutors:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()
        cls.audit = ExecutorAuditLog(cls.store)

    def test_browser_not_implemented(self):
        e = BrowserExecutor(self.audit)
        result = e.execute("navigate", {"url": "http://example.com"})
        assert not result["ok"]
        assert "not implemented" in result["error"]

    def test_api_not_implemented(self):
        e = APIExecutor(self.audit)
        result = e.execute("call", {"endpoint": "/test"})
        assert not result["ok"]

    def test_comms_not_implemented(self):
        e = CommsExecutor(self.audit)
        result = e.execute("send", {"to": "test"})
        assert not result["ok"]
