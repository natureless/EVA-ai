"""Audit entries with identical clock timestamps retain their append order."""

from datetime import datetime, timezone

import pytest

import core.executor as executor_module
from core.executor import ExecutorAuditLog
from memory.sqlite_store import SQLiteStore


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"executor_type": "file"},
        {"status": "success"},
        {"executor_type": "file", "status": "success"},
    ],
)
def test_equal_timestamps_query_latest_and_continue_chain(
    tmp_path, monkeypatch, filters
):
    class FixedTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(executor_module, "datetime", FixedTime)
    store = SQLiteStore(tmp_path / "audit.db")
    store.init_db()
    try:
        audit = ExecutorAuditLog(store)
        ids = [
            audit.record(executor_type="file", action="search", task_id=f"task-{i}")
            for i in range(3)
        ]
        assert [row["id"] for row in audit.query(**filters)] == ids[::-1]
        assert audit.query(limit=1, **filters)[0]["id"] == ids[-1]

        # A new logger must extend the last appended hash, not an earlier tie.
        reopened = ExecutorAuditLog(store)
        ids.append(
            reopened.record(executor_type="file", action="search", task_id="last")
        )
        assert reopened.query(limit=1, **filters)[0]["id"] == ids[-1]
        assert reopened.verify_chain() == (True, "chain verified (4 entries)")
    finally:
        store.close()
