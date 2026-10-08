"""Durable drafts, canonical sealing, fault rollback and actual process recovery."""

from types import SimpleNamespace
import json
import sqlite3
import subprocess
import sys

import pytest

from event.event_schema import Event
from memory.episodes import (
    EpisodeRecord,
    EpisodeSchemaError,
    StateReference,
    read_episode,
)
from packages.kernel.processing_episodes import ProcessingEpisodes, world_reference


def event():
    return Event(
        id="event-a",
        type="user_message",
        source="test",
        payload={"text": "private text"},
        correlation_id="task-a",
    )


def receipt(terminal="succeeded", **extra):
    return {
        "event_id": "event-a",
        "task_id": "task-a",
        "terminal_state": terminal,
        "reply": "private reply",
        **extra,
    }


def test_v2_unknown_revision_is_explicit_and_v1_does_not_accept_null():
    v2 = EpisodeRecord(
        schema_version=2,
        event_id="e",
        event_type="chat",
        source="test",
        state_before=StateReference(version=None, integrity_hash="hash"),
    )
    assert read_episode(v2.as_record()).state_before.version is None
    with pytest.raises(EpisodeSchemaError):
        read_episode({**v2.as_record(), "schema_version": 1})
    with pytest.raises(EpisodeSchemaError):
        read_episode({**v2.as_record(), "schema_version": True})
    assert world_reference({"a": 1, "b": 2}) == world_reference({"b": 2, "a": 1})
    assert world_reference({"a": 1}) != world_reference({"a": 2})


def test_journal_tracks_invocation_without_copying_payloads_or_tool_claims(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        assert journal.begin(event(), "loop-a", {"focus": "private"})
        assert not journal.begin(event(), "other-loop", {})
        journal.action_started("event-a", "loop-a", "chat_agent")
        journal.action_finished("event-a", "loop-a", SimpleNamespace(ok=True))
        journal.checkpoint("event-a", "loop-a", {"focus": "changed"})
        journal.record_receipt(receipt())
        saved = journal.get_by_event("event-a")
        assert saved.schema_version == 2 and saved.state_before.version is None
        assert (
            saved.state_after.version is None
            and saved.state_after.integrity_hash != saved.state_before.integrity_hash
        )
        assert (
            saved.result.status == "succeeded"
            and saved.actions[0].status == "completed"
        )
        assert (
            saved.actions[0].kind == "agent_invocation"
            and saved.actions[0].tool_id == ""
        )
        assert saved.result.receipt_id == "" and saved.metadata["task_id"] == "task-a"
        assert "private" not in json.dumps(saved.as_record())
        journal.record_receipt(receipt("failed"))
        journal.action_finished("event-a", "loop-a", SimpleNamespace(ok=False))
        assert journal.get_by_event("event-a").as_record() == saved.as_record()
        assert journal.stats()["processing"] == 0 and journal.recover() == 0
        assert not journal.begin(event(), "new-loop", {})
    finally:
        journal.close()
        journal.close()


def test_unknown_first_terminal_cannot_be_upgraded_by_late_agent_result(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        journal.begin(event(), "loop-a", {})
        journal.action_started("event-a", "loop-a", "chat_agent")
        journal.record_receipt(receipt("outcome_unknown"))
        journal.action_finished("event-a", "loop-a", SimpleNamespace(ok=True))
        journal.checkpoint("event-a", "loop-a", {"later": True})
        journal.record_receipt(receipt("succeeded"))
        saved = journal.get_by_event("event-a")
        assert saved.result.status == "unknown" and saved.result.ok is None
        assert saved.actions[0].status == "unknown" and saved.state_after is None
        assert saved.metadata["completion_kind"] == "canonical_receipt"
    finally:
        journal.close()


def test_forged_task_and_loop_do_not_update_existing_draft(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        journal.begin(event(), "loop-a", {})
        before = journal._conn.execute(
            "SELECT record_json FROM episode_processing"
        ).fetchone()[0]
        journal.action_started("event-a", "other-loop", "wrong_agent")
        journal.checkpoint("event-a", "other-loop", {"fake": 1})
        journal.record_receipt(receipt(task_id="other-task"))
        after = journal._conn.execute(
            "SELECT record_json FROM episode_processing"
        ).fetchone()[0]
        assert before == after and journal.get_by_event("event-a") is None
    finally:
        journal.close()


def test_seal_failure_rolls_back_insert_and_keeps_recoverable_draft(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        journal.begin(event(), "loop-a", {})
        journal._conn.set_authorizer(
            lambda op, table, *args: sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_DELETE and table == "episode_processing"
            else sqlite3.SQLITE_OK
        )
        with pytest.raises(sqlite3.DatabaseError):
            journal.record_receipt(receipt())
        assert (
            not journal._conn.in_transaction and journal.get_by_event("event-a") is None
        )
        assert journal.stats()["processing"] == 1
        journal._conn.set_authorizer(None)
        journal.record_receipt(receipt())
        assert journal.get_by_event("event-a").result.status == "succeeded"
    finally:
        journal.close()


def test_recovery_batch_is_atomic_for_corrupted_draft(tmp_path):
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        journal.begin(event(), "loop-a", {})
        second = event().model_copy(
            update={"id": "event-b", "correlation_id": "task-b"}
        )
        journal.begin(second, "loop-b", {})
        journal._conn.execute(
            "UPDATE episode_processing SET record_json='broken' WHERE event_id='event-b'"
        )
        journal._conn.commit()
        with pytest.raises(ValueError):
            journal.recover()
        assert not journal._conn.in_transaction
        assert journal.stats() == {
            "episodes": 0,
            "subjects": 0,
            "processing": 2,
            "receipt_notifications_pending": 0,
            "receipt_delivery_errors": 0,
        }
    finally:
        journal.close()


def test_actual_process_crash_recovers_unknown_without_action_replay(tmp_path):
    path = tmp_path / "eva.db"
    script = """
import os, sys
from event.event_schema import Event
from packages.kernel.processing_episodes import ProcessingEpisodes
j = ProcessingEpisodes(sys.argv[1])
e = Event(id="crash-event", type="user_message", source="test", payload={}, correlation_id="task")
j.begin(e, "loop", {})
j.action_started(e.id, "loop", "chat_agent")
os._exit(37)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, timeout=15
    )
    assert process.returncode == 37
    journal = ProcessingEpisodes(path)
    try:
        assert journal.stats()["processing"] == 1
        assert journal.recover() == 1 and journal.stats()["processing"] == 0
        saved = journal.get_by_event("crash-event")
        assert saved.result.status == "unknown" and saved.actions[0].status == "unknown"
        assert saved.metadata["completion_kind"] == "recovery_unknown"
        assert journal.replay(saved.episode_id)["external_actions_replayed"] is False
        assert journal.recover() == 0
    finally:
        journal.close()
