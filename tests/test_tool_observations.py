"""Real file effects, independent samples, crash recovery and append fences."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from event.event_schema import Event
from memory.tool_observations import read_tool_observation
from memory.tool_receipts import UnresolvedToolCall
from packages.kernel.processing_episodes import ProcessingEpisodes
from packages.minimal_brain.business_goal_store import BusinessGoalStore
from packages.minimal_brain.file_verifier import WorkspaceFileVerifier


@pytest.fixture
def journal(tmp_path):
    store = ProcessingEpisodes(
        tmp_path / "eva.db", file_verifier=WorkspaceFileVerifier(tmp_path)
    )
    try:
        yield store
    finally:
        store.close()


def unknown_write(
    journal,
    tmp_path,
    *,
    content="private\ntext",
    write=True,
    tool_id="executor:file:write",
    path=None,
):
    event = Event(id="event", type="user_message", source="test", correlation_id="task")
    journal.begin(event, "loop", {})
    journal.action_started(event.id, "loop", "chat_agent")
    target = path or tmp_path / "result.txt"
    args = {"path": str(target), "content": content}
    action = journal.tool_started("event", "loop", tool_id, args)
    if write:
        target.write_text(str(content), encoding="utf-8")
    journal.record_receipt(
        {
            "event_id": "event",
            "task_id": "task",
            "terminal_state": "outcome_unknown",
            "ok": False,
        }
    )
    return action, target, args


def test_samples_append_without_changing_episode_receipt_goal_or_replaying(
    journal, tmp_path
):
    action, target, args = unknown_write(journal, tmp_path)
    receipt = journal.tool_receipts("event")[0]
    episode = journal.get_by_event("event").as_record()
    goals = BusinessGoalStore(journal._db_path)
    try:
        goal = goals.create(
            task_id="task",
            source_event_id="event",
            description="independent",
            success_conditions=[{"field": "checks.files_match", "expected": True}],
        )
        first = journal.reconcile_tool("event", action, expected_count=0)
        assert first["outcome"] == "passed" and first["sequence"] == 1
        assert (
            first["files"][0]["observed_sha256"]
            == hashlib.sha256(target.read_bytes()).hexdigest()
        )
        target.write_text("changed", encoding="utf-8")
        second = journal.reconcile_tool("event", action, expected_count=1)
        assert second["outcome"] == "mismatch"
        target.unlink()
        third = journal.reconcile_tool("event", action, expected_count=2)
        assert (
            third["outcome"] == "mismatch" and third["files"][0]["outcome"] == "missing"
        )
        assert journal.tool_receipts("event")[0] == receipt
        assert journal.get_by_event("event").as_record() == episode
        assert goals.get(goal["goal_id"]) == goal
        history = journal.observation_history("event", action)
        assert history["records"] == [first, second, third] and history["count"] == 3
        assert "private" not in json.dumps(history)
        reference = goals.episode_reference(goal["goal_id"])["episode"][
            "tool_receipts"
        ]["receipts"][0]
        assert (
            reference["status"] == "unknown"
            and reference["reconciliation"]["records"] == history["records"]
        )
        # A positive file sample does not authorize an identical unresolved retry.
        journal.begin(
            Event(id="other", type="user_message", source="test"), "other", {}
        )
        # Existing sealed source has no draft and cannot dispatch again.
        with pytest.raises(ValueError):
            journal.tool_started("event", "loop", "executor:file:write", args)
    finally:
        goals.close()


def test_checking_unknown_in_live_draft_does_not_unblock_retry(journal, tmp_path):
    journal.begin(Event(id="event", type="user_message", source="test"), "loop", {})
    journal.action_started("event", "loop", "chat_agent")
    args = {"path": str(tmp_path / "result.txt"), "content": "already happened"}
    action = journal.tool_started("event", "loop", "executor:file:write", args)
    (tmp_path / "result.txt").write_text(args["content"], encoding="utf-8")
    journal.tool_finished(
        "event", "loop", action, returned_ok=False, observation_kind="handler_return"
    )
    assert (
        journal.reconcile_tool("event", action, expected_count=0)["outcome"] == "passed"
    )
    with pytest.raises(UnresolvedToolCall):
        journal.tool_started("event", "loop", "executor:file:write", args)


def test_capture_matches_utf8_and_native_text_newlines(journal, tmp_path):
    action, target, _ = unknown_write(journal, tmp_path, content="你好\nline\r\n")
    captured = journal.observation_history("event", action)["intent"]
    assert (
        captured["expected_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    )
    assert (
        journal.reconcile_tool("event", action, expected_count=0)["outcome"] == "passed"
    )


@pytest.mark.parametrize(
    "tool_id,path",
    [
        ("tool:custom", "result.txt"),
        ("executor:file:read", "result.txt"),
        ("executor:file:write", ".secret"),
        ("executor:file:write", "outside"),
    ],
)
def test_unsupported_calls_have_no_inferred_checker(journal, tmp_path, tool_id, path):
    target = tmp_path.parent / "outside" if path == "outside" else tmp_path / path
    action, _, _ = unknown_write(
        journal, tmp_path, write=False, tool_id=tool_id, path=target
    )
    assert journal.observation_history("event", action)["status"] == "unsupported"
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=0)


def test_old_intent_is_not_reconstructed_from_hash_only(journal, tmp_path):
    journal.file_verifier = None
    action, _, _ = unknown_write(journal, tmp_path)
    journal.file_verifier = WorkspaceFileVerifier(tmp_path)
    assert journal.observation_history("event", action)["status"] == "unsupported"


def test_capture_failure_rolls_back_intent_receipt_and_draft(journal, tmp_path):
    journal.begin(Event(id="event", type="user_message", source="test"), "loop", {})
    journal.action_started("event", "loop", "chat_agent")
    journal._conn.set_authorizer(
        lambda op, table, *a: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "episode_tool_file_intents"
        else sqlite3.SQLITE_OK
    )
    with pytest.raises(sqlite3.Error):
        journal.tool_started(
            "event",
            "loop",
            "executor:file:write",
            {"path": str(tmp_path / "result.txt")},
        )
    journal._conn.set_authorizer(None)
    assert journal.tool_receipts("event") == []
    assert len(journal._draft("event").actions) == 1


def test_observation_insert_failure_leaves_no_partial_sample(journal, tmp_path):
    action, _, _ = unknown_write(journal, tmp_path)
    journal._conn.set_authorizer(
        lambda op, table, *a: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "episode_tool_observations"
        else sqlite3.SQLITE_OK
    )
    with pytest.raises(sqlite3.Error):
        journal.reconcile_tool("event", action, expected_count=0)
    journal._conn.set_authorizer(None)
    assert journal.observation_history("event", action)["count"] == 0
    assert journal.reconcile_tool("event", action, expected_count=0)["sequence"] == 1


def test_sampling_releases_lock_and_two_connections_commit_only_one(
    journal, tmp_path, monkeypatch
):
    action, _, _ = unknown_write(journal, tmp_path)
    second = ProcessingEpisodes(
        journal._db_path, file_verifier=WorkspaceFileVerifier(tmp_path)
    )
    barrier = Barrier(2)
    original = WorkspaceFileVerifier.run

    def sample(verifier, spec):
        barrier.wait(timeout=5)
        assert not journal._conn.in_transaction and not second._conn.in_transaction
        return original(verifier, spec)

    monkeypatch.setattr(WorkspaceFileVerifier, "run", sample)

    def check(store):
        try:
            return store.reconcile_tool("event", action, expected_count=0)["outcome"]
        except ValueError:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(check, [journal, second]))
        assert sorted(results) == ["conflict", "passed"]
        assert journal.observation_history("event", action)["count"] == 1
    finally:
        second.close()


def test_changed_workspace_rejects_sampling_and_retains_historical_sample(
    journal, tmp_path
):
    action, _, _ = unknown_write(journal, tmp_path)
    saved = journal.reconcile_tool("event", action, expected_count=0)
    other = tmp_path / "other"
    other.mkdir()
    journal.file_verifier = WorkspaceFileVerifier(other)
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=1)
    assert journal.observation_history("event", action)["records"] == [saved]


def test_history_budget_and_graph_projection_are_bounded(journal, tmp_path):
    action, _, _ = unknown_write(journal, tmp_path)
    for count in range(32):
        journal.reconcile_tool("event", action, expected_count=count)
    assert journal.observation_history("event", action)["count"] == 32
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=32)
    goals = BusinessGoalStore(journal._db_path)
    try:
        goal = goals.create(
            task_id="task",
            source_event_id="event",
            description="bounded",
            success_conditions=[{"field": "checks.ready", "expected": True}],
        )
        before = goals._conn.total_changes
        projected = goals.episode_reference(goal["goal_id"])["episode"][
            "tool_receipts"
        ]["receipts"][0]["reconciliation"]
        assert projected["count"] == 32 and projected["truncated"]
        assert [r["sequence"] for r in projected["records"]] == [30, 31, 32]
        assert goals._conn.total_changes == before
    finally:
        goals.close()


def test_started_or_completed_receipts_cannot_trigger_file_check(
    journal, tmp_path, monkeypatch
):
    journal.begin(Event(id="event", type="user_message", source="test"), "loop", {})
    journal.action_started("event", "loop", "chat_agent")
    action = journal.tool_started(
        "event", "loop", "executor:file:write", {"path": str(tmp_path / "result.txt")}
    )
    monkeypatch.setattr(
        journal.file_verifier, "run", lambda spec: pytest.fail("must not sample")
    )
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=0)
    journal.tool_finished(
        "event", "loop", action, returned_ok=True, observation_kind="handler_return"
    )
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=0)


def test_file_too_large_is_unknown_not_failed_execution(journal, tmp_path):
    action, target, _ = unknown_write(journal, tmp_path)
    target.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    sampled = journal.reconcile_tool("event", action, expected_count=0)
    assert sampled["outcome"] == "unknown"
    assert sampled["files"][0]["reason"] == "file_too_large"
    assert journal.tool_receipts("event")[0].status == "unknown"


def test_intent_change_during_sampling_prevents_commit(journal, tmp_path, monkeypatch):
    action, _, _ = unknown_write(journal, tmp_path)
    original = journal.file_verifier.run

    def sample(spec):
        result = original(spec)
        row = journal._conn.execute(
            "SELECT record_json FROM episode_tool_file_intents WHERE action_id=?",
            (action,),
        ).fetchone()
        intent = json.loads(row[0])
        journal._conn.execute(
            "UPDATE episode_tool_file_intents SET record_json=? WHERE action_id=?",
            (json.dumps({**intent, "expected_sha256": "0" * 64}), action),
        )
        journal._conn.commit()
        return result

    monkeypatch.setattr(journal.file_verifier, "run", sample)
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=0)
    assert (
        journal._conn.execute(
            "SELECT COUNT(*) FROM episode_tool_observations"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("count", [True, -1, 32, "0", 1])
def test_invalid_or_stale_count_does_not_sample(journal, tmp_path, monkeypatch, count):
    action, _, _ = unknown_write(journal, tmp_path)
    monkeypatch.setattr(
        journal.file_verifier, "run", lambda spec: pytest.fail("must not sample")
    )
    with pytest.raises(ValueError):
        journal.reconcile_tool("event", action, expected_count=count)


def test_identity_tampering_and_oversized_observation_are_unavailable(
    journal, tmp_path
):
    action, _, _ = unknown_write(journal, tmp_path)
    saved = journal.reconcile_tool("event", action, expected_count=0)
    goals = BusinessGoalStore(journal._db_path)
    try:
        goal = goals.create(
            task_id="task",
            source_event_id="event",
            description="readonly",
            success_conditions=[{"field": "checks.ready", "expected": True}],
        )
        journal._conn.execute(
            "UPDATE episode_tool_observations SET record_json=?",
            (json.dumps({**saved, "request_hash": "0" * 64}),),
        )
        journal._conn.commit()
        with pytest.raises(ValueError):
            journal.observation_history("event", action)
        reference = goals.episode_reference(goal["goal_id"])
        assert reference["status"] == "available"
        tool = reference["episode"]["tool_receipts"]["receipts"][0]
        assert (
            tool["status"] == "unknown"
            and tool["reconciliation"]["status"] == "unavailable"
        )
        journal._conn.execute(
            "UPDATE episode_tool_observations SET record_json=?", ("x" * 9000,)
        )
        journal._conn.commit()
        with pytest.raises(ValueError):
            journal.observation_history("event", action)
    finally:
        goals.close()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"sequence": True},
        {"observed_at": "2020-01-01"},
        {"outcome": "mismatch"},
        {"files": []},
        {"effect_completed": True},
    ],
)
def test_strict_codec_rejects_invalid_claims(journal, tmp_path, change):
    action, _, _ = unknown_write(journal, tmp_path)
    saved = journal.reconcile_tool("event", action, expected_count=0)
    with pytest.raises(ValueError):
        read_tool_observation(json.dumps({**saved, **change}))


def test_actual_executor_write_then_process_exit_recovers_and_samples_without_replay(
    tmp_path,
):
    script = """
import os,sys
from pathlib import Path
from types import SimpleNamespace
from core.executor import FileExecutor
from core.tool_execution import tool_execution_scope
from event.event_schema import Event
from packages.kernel.processing_episodes import ProcessingEpisodes
from packages.minimal_brain.file_verifier import WorkspaceFileVerifier
root=Path(sys.argv[1]);j=ProcessingEpisodes(root/"eva.db",file_verifier=WorkspaceFileVerifier(root))
j.begin(Event(id="event",type="user_message",source="test",correlation_id="task"),"loop",{})
j.action_started("event","loop","chat_agent")
class CrashFile(FileExecutor):
    def _run(self,action,params):
        super()._run(action,params)
        os._exit(47)
executor=CrashFile(SimpleNamespace(record=lambda **kw: None),{"executors":{"file":{"allowed_paths":[str(root)]}}})
with tool_execution_scope({"causation_id":"event","loop_id":"loop"},j):
    executor.execute("write",{"path":str(root/"effect.txt"),"content":"real\\nbytes"})
"""
    exited = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=15
    )
    assert exited.returncode == 47, exited.stderr.decode(errors="replace")
    journal = ProcessingEpisodes(
        tmp_path / "eva.db", file_verifier=WorkspaceFileVerifier(tmp_path)
    )
    try:
        assert journal.recover() == 1
        receipt = journal.tool_receipts("event")[0]
        assert receipt.status == "unknown" and receipt.finished_at is None
        assert (
            journal.reconcile_tool("event", receipt.action_id, expected_count=0)[
                "outcome"
            ]
            == "passed"
        )
        assert journal.tool_receipts("event")[0] == receipt
        assert journal.recover() == 0
    finally:
        journal.close()
