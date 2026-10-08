"""Graph reads must preserve state, keep real evidence, and remain bounded."""

from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from packages.minimal_brain.business_goal_store import BusinessGoalStore
from packages.minimal_brain.file_verifier import WorkspaceFileVerifier


def create(store, index=0, **kwargs):
    return store.create(
        task_id=f"task-{index}",
        source_event_id=f"event-{index}",
        description=f"目标 {index}",
        success_conditions=[{"field": "checks.files_match", "expected": True}],
        **kwargs,
    )


def test_graph_is_read_only_even_after_deadline_and_does_not_recover(tmp_path):
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    store = BusinessGoalStore(tmp_path / "eva.db", clock=lambda: now[0])
    try:
        goal = create(store, deadline=now[0] + timedelta(seconds=1))
        before = store._conn.total_changes
        now[0] += timedelta(seconds=2)
        queries = []
        store._conn.set_trace_callback(queries.append)
        projection = store.graph()
        node = projection["nodes"][0]
        assert node["record"]["status"] == "active"
        assert node["record"]["version"] == goal["version"]
        assert store._conn.total_changes == before
        assert not any(
            q.startswith(("UPDATE", "INSERT", "DELETE", "BEGIN IMMEDIATE"))
            for q in queries
        )
        assert projection["edges"] == []
        assert projection["scope"]["read_only"] is True
    finally:
        store.close()


def test_bounded_pages_literal_search_and_closed_store(tmp_path):
    store = BusinessGoalStore(tmp_path / "eva.db")
    try:
        ids = [create(store, i)["goal_id"] for i in range(43)]
        first, second = store.graph(), store.graph(offset=40)
        assert len(first["nodes"]) == 40 and len(second["nodes"]) == 3
        assert first["next_offset"] == 40 and second["next_offset"] is None
        assert [n["record_id"] for n in first["nodes"] + second["nodes"]] == ids[::-1]
        assert first["scope"]["matched_goals"] == 43
        assert len(store.graph(query="目标 42")["nodes"]) == 1
        assert len(store.graph(query="event-42")["nodes"]) == 1
        assert store.graph(query="%' OR 1=1 --")["nodes"] == []
        assert store.graph(offset=43)["next_offset"] is None
        for args in (
            {"limit": 41},
            {"limit": True},
            {"offset": -1},
            {"query": "x" * 201},
        ):
            with pytest.raises(ValueError):
                store.graph(**args)
    finally:
        store.close()
    with pytest.raises(RuntimeError):
        store.graph()


def test_graph_keeps_latest_five_real_checks_and_historical_pass(tmp_path, monkeypatch):
    verifier = WorkspaceFileVerifier(tmp_path)
    store = BusinessGoalStore(tmp_path / "eva.db", verifier=verifier)
    try:
        expected = hashlib.sha256(b"good").hexdigest()
        goal = create(
            store,
            verification={
                "kind": "workspace_files_sha256_v1",
                "files": [{"path": "result.txt", "sha256": expected}],
            },
        )
        store.record_receipt(
            {
                "task_id": "task-0",
                "event_id": "event-0",
                "terminal_state": "succeeded",
                "ok": True,
            }
        )
        goal = store.get(goal["goal_id"])
        for _ in range(6):
            goal = store.verify(goal["goal_id"], expected_version=goal["version"])
        (tmp_path / "result.txt").write_bytes(b"good")
        done = store.verify(goal["goal_id"], expected_version=goal["version"])
        (tmp_path / "result.txt").write_bytes(b"changed after verification")

        def forbidden(*args, **kwargs):
            pytest.fail("graph must not inspect the filesystem or update the store")

        monkeypatch.setattr(verifier, "run", forbidden)
        monkeypatch.setattr(store, "get", forbidden)
        monkeypatch.setattr(store, "record_receipt", forbidden)
        graph = store.graph()
        assert len(graph["nodes"]) == 7 and len(graph["edges"]) == 6
        g, receipt, latest, *older = graph["nodes"]
        assert g["status"] == "completed" and receipt["status"] == "succeeded"
        assert g["verification_count"] == 7 and g["history_truncated"]
        assert g["record"]["verification"]["files"][0]["sha256"] == expected
        assert receipt["timestamp"] is None
        assert latest["status"] == "passed"
        assert latest["record"] == done["last_verification"]
        assert all(n["status"] == "mismatch" for n in older)
        ids = {n["id"] for n in graph["nodes"]}
        assert all(
            e["source"] == g["id"] and e["target"] in ids for e in graph["edges"]
        )
        assert all(e["kind"] == "goal_evidence" for e in graph["edges"])
        assert graph["scope"]["history_truncated"]
    finally:
        store.close()


def test_verification_history_is_bounded_read_only_and_version_ordered(tmp_path):
    store = BusinessGoalStore(tmp_path / "eva.db")
    try:
        goal = create(store)
        rows = []
        for version in (2, 3, 4):
            rows.append(
                (
                    f"run-{version}",
                    goal["goal_id"],
                    version,
                    store._encode(
                        {
                            "schema_version": 1,
                            "run_id": f"run-{version}",
                            "goal_id": goal["goal_id"],
                            "outcome": "mismatch" if version < 4 else "passed",
                            "observed_at": f"2030-01-0{version}T00:00:00+00:00",
                        }
                    ),
                )
            )
        store._conn.executemany(
            "INSERT INTO business_goal_verifications VALUES (?,?,?,?)", rows
        )
        store._conn.commit()
        before = store._conn.total_changes
        page = store.verification_history(goal["goal_id"], limit=2)
        assert [item["run_id"] for item in page["records"]] == ["run-4", "run-3"]
        assert page["total"] == 3 and page["next_offset"] == 2
        assert page["read_only"] is True and store._conn.total_changes == before
        assert (
            store.verification_history(goal["goal_id"], offset=2)["next_offset"] is None
        )
        for kwargs in ({"limit": 51}, {"limit": True}, {"offset": -1}):
            with pytest.raises(ValueError):
                store.verification_history(goal["goal_id"], **kwargs)
        with pytest.raises(KeyError):
            store.verification_history("missing")
    finally:
        store.close()


def test_read_failure_rolls_back_snapshot_without_poisoning_connection(tmp_path):
    store = BusinessGoalStore(tmp_path / "eva.db")
    try:
        goal = create(store)
        original = store._conn.execute(
            "SELECT record_json FROM business_goals"
        ).fetchone()[0]
        store._conn.execute("UPDATE business_goals SET record_json='broken'")
        store._conn.commit()
        with pytest.raises(sqlite3.Error):
            store.graph()
        assert not store._conn.in_transaction
        store._conn.execute("UPDATE business_goals SET record_json=?", (original,))
        store._conn.commit()
        assert store.graph()["nodes"][0]["record_id"] == goal["goal_id"]
    finally:
        store.close()
