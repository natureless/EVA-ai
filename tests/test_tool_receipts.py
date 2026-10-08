"""Real handler effects, intent failures, atomic observations and crash windows."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from core.tool_execution import tool_execution_scope
from core.tool_registry import ToolDef, ToolRegistry, execute_tool
from event.event_schema import Event
from memory.tool_receipts import read_tool_receipt
from packages.kernel.processing_episodes import ProcessingEpisodes
from packages.minimal_brain.business_goal_store import BusinessGoalStore


@pytest.fixture
def journal(tmp_path):
    store = ProcessingEpisodes(tmp_path / "eva.db")
    try:
        yield store
    finally:
        store.close()


def begin(journal, event_id="event", loop="loop"):
    event = Event(
        id=event_id,
        type="user_message",
        source="test",
        correlation_id=f"task-{event_id}",
    )
    assert journal.begin(event, loop, {})
    journal.action_started(event_id, loop, "chat_agent")
    return {"causation_id": event_id, "loop_id": loop}


def registry(handler, *, authorized=False):
    tools = ToolRegistry()
    tools.register(
        ToolDef(
            name="probe",
            description="test",
            parameters={},
            handler=handler,
            requires_user_authorization=authorized,
        )
    )
    return tools


def seal(journal, event_id="event", state="succeeded"):
    journal.record_receipt(
        {
            "event_id": event_id,
            "task_id": f"task-{event_id}",
            "terminal_state": state,
            "ok": state == "succeeded",
        }
    )


def test_real_write_has_hashed_request_and_observed_return_without_payload_copy(
    journal, tmp_path
):
    trace = begin(journal)
    file = tmp_path / "effect.txt"

    def write(secret):
        assert not journal._conn.in_transaction
        assert journal.tool_receipts("event")[0].status == "started"
        file.write_text(secret, encoding="utf-8")
        return {"ok": True, "content": secret}

    with tool_execution_scope(trace, journal):
        result = execute_tool("probe", {"secret": "private-content"}, registry(write))
    assert result["ok"] and file.read_text() == "private-content"
    receipt = journal.tool_receipts("event")[0]
    assert receipt.status == "completed" and receipt.returned_ok is True
    assert (
        receipt.finished_at is not None and receipt.observation_kind == "handler_return"
    )
    assert "private-content" not in receipt.model_dump_json()
    seal(journal)
    saved = journal.get_by_event("event")
    assert saved.actions[1].receipt_id == receipt.receipt_id
    goals = BusinessGoalStore(journal._db_path)
    try:
        g = goals.create(
            task_id="task-event",
            source_event_id="event",
            description="trace",
            success_conditions=[{"field": "checks.ready", "expected": True}],
        )
        before = goals._conn.total_changes
        projected = goals.episode_reference(g["goal_id"])["episode"]["tool_receipts"]
        assert projected["status"] == "available"
        assert projected["receipts"][0]["request_hash"] == receipt.request_hash
        assert goals._conn.total_changes == before
        journal._conn.execute("UPDATE episode_tool_receipts SET record_json='broken'")
        journal._conn.commit()
        reference = goals.episode_reference(g["goal_id"])
        assert (
            reference["status"] == "available"
            and reference["episode"]["tool_receipts"]["status"] == "unavailable"
        )
        assert goals.graph()["counts"]["E"]["total"] == 1
    finally:
        goals.close()


def test_intent_failure_blocks_handler(journal):
    trace = begin(journal)
    calls = []
    journal._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "episode_tool_receipts"
        else sqlite3.SQLITE_OK
    )
    with tool_execution_scope(trace, journal):
        result = execute_tool(
            "probe", {}, registry(lambda: calls.append(True) or {"ok": True})
        )
    assert result["execution_state"] == "not_started" and calls == []
    assert journal.tool_receipts("event") == []
    journal._conn.set_authorizer(None)


def test_return_record_failure_preserves_effect_as_unknown_and_does_not_retry(
    journal, tmp_path
):
    trace = begin(journal)
    calls = []

    def write():
        calls.append(True)
        (tmp_path / "effect").write_text("done")
        journal._conn.set_authorizer(
            lambda op, table, *args: sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_UPDATE and table == "episode_tool_receipts"
            else sqlite3.SQLITE_OK
        )
        return {"ok": True}

    with tool_execution_scope(trace, journal):
        result = execute_tool("probe", {}, registry(write))
    assert result["execution_state"] == "outcome_unknown" and len(calls) == 1
    assert journal.tool_receipts("event")[0].status == "started"
    journal._conn.set_authorizer(None)
    with tool_execution_scope(trace, journal):
        repeated = execute_tool("probe", {}, registry(write))
    assert repeated["previous_outcome_unknown"] is True and len(calls) == 1
    assert journal.recover() == 1
    unknown = journal.tool_receipts("event")[0]
    assert unknown.status == "unknown" and unknown.finished_at is None
    assert unknown.observation_kind == "seal_unknown"
    assert (tmp_path / "effect").read_text() == "done" and len(calls) == 1


@pytest.mark.parametrize("throws", [False, True])
def test_failed_return_or_exception_does_not_prove_external_effect_failed(
    journal, throws
):
    trace = begin(journal)
    effects = []

    def handler():
        effects.append(True)
        if throws:
            raise RuntimeError("after effect")
        return {"ok": False}

    with tool_execution_scope(trace, journal):
        assert execute_tool("probe", {}, registry(handler))["ok"] is False
    receipt = journal.tool_receipts("event")[0]
    assert effects == [True] and receipt.status == "unknown"
    assert receipt.returned_ok is (None if throws else False)
    assert receipt.observation_kind == (
        "handler_exception" if throws else "handler_return"
    )


def test_first_unknown_seal_blocks_late_updates_and_further_tool_effects(journal):
    trace = begin(journal)

    def running():
        seal(journal, state="outcome_unknown")
        return {"ok": True}

    with tool_execution_scope(trace, journal):
        assert execute_tool("probe", {}, registry(running))["ok"] is True
        calls = []
        assert (
            execute_tool(
                "probe", {}, registry(lambda: calls.append(True) or {"ok": True})
            )["execution_state"]
            == "not_started"
        )
    assert calls == []
    receipt = journal.tool_receipts("event")[0]
    assert (
        receipt.status == "unknown"
        and receipt.returned_ok is None
        and receipt.finished_at is None
    )
    assert journal.get_by_event("event").result.status == "unknown"


def test_sealing_failure_rolls_back_tool_unknown_observation(journal):
    begin(journal)
    action = journal.tool_started("event", "loop", "tool:probe", {})
    journal._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "episode_receipt_outbox"
        else sqlite3.SQLITE_OK
    )
    with pytest.raises(sqlite3.DatabaseError):
        seal(journal, state="outcome_unknown")
    assert journal.tool_receipts("event")[0].status == "started"
    assert journal.get_by_event("event") is None
    journal._conn.set_authorizer(None)
    seal(journal, state="outcome_unknown")
    assert journal.tool_receipts("event")[0].action_id == action
    assert journal.tool_receipts("event")[0].status == "unknown"


def test_thread_context_separates_concurrent_calls_and_restores_scope(journal):
    barrier = Barrier(2)
    for index in range(2):
        begin(journal, f"event-{index}", f"loop-{index}")

    def run(index):
        with tool_execution_scope(
            {"causation_id": f"event-{index}", "loop_id": f"loop-{index}"}, journal
        ):
            result = execute_tool(
                "probe",
                {},
                registry(
                    lambda: barrier.wait(timeout=5) and {"ok": True} or {"ok": True}
                ),
            )
            assert result["ok"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, range(2)))
    for index in range(2):
        receipt = journal.tool_receipts(f"event-{index}")[0]
        assert (
            receipt.event_id == f"event-{index}" and receipt.loop_id == f"loop-{index}"
        )
    assert execute_tool("probe", {}, registry(lambda: {"ok": True}))["ok"]
    assert len(journal.tool_receipts("event-0")) == 1


def test_authorization_denial_records_no_handler_execution(journal):
    with tool_execution_scope(begin(journal), journal):
        result = execute_tool(
            "probe",
            {},
            registry(lambda: pytest.fail("denied handler"), authorized=True),
        )
    assert result["denied"] is True and journal.tool_receipts("event") == []


def test_real_process_exit_after_file_effect_restores_unknown_without_repeat(tmp_path):
    path = tmp_path / "eva.db"
    effect = tmp_path / "effect"
    script = """
import os,sys
from pathlib import Path
from event.event_schema import Event
from packages.kernel.processing_episodes import ProcessingEpisodes
from core.tool_execution import tool_execution_scope
from core.tool_registry import ToolRegistry,ToolDef,execute_tool
j=ProcessingEpisodes(sys.argv[1])
e=Event(id="event",type="user_message",source="test",correlation_id="task-event")
j.begin(e,"loop",{});j.action_started(e.id,"loop","chat_agent")
def write():
    Path(sys.argv[2]).write_text("one-effect")
    os._exit(43)
r=ToolRegistry();r.register(ToolDef("probe","",{},write))
with tool_execution_scope({"causation_id":"event","loop_id":"loop"},j):
    execute_tool("probe",{},r)
"""
    exited = subprocess.run(
        [sys.executable, "-c", script, str(path), str(effect)],
        capture_output=True,
        timeout=15,
    )
    assert exited.returncode == 43, exited.stderr.decode(errors="replace")
    journal = ProcessingEpisodes(path)
    try:
        assert journal.tool_receipts("event")[0].status == "started"
        assert journal.recover() == 1
        restored = journal.tool_receipts("event")[0]
        assert restored.status == "unknown" and restored.finished_at is None
        assert effect.read_text() == "one-effect"
        assert journal.recover() == 0
        assert journal.get_by_event("event").actions[1].status == "unknown"
    finally:
        journal.close()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 99},
        {"returned_ok": 1},
        {"started_at": "2030-01-01"},
        {"status": "completed"},
    ],
)
def test_receipt_rejects_unproven_or_invalid_observation(journal, change):
    begin(journal)
    journal.tool_started("event", "loop", "tool:probe", {})
    encoded = journal.tool_receipts("event")[0].model_dump(mode="json")
    with pytest.raises(ValueError):
        read_tool_receipt(json.dumps({**encoded, **change}))


def test_action_budget_and_wrong_owner_block_further_handler_calls(journal):
    trace = begin(journal)
    with pytest.raises(ValueError):
        journal.tool_started("event", "other-loop", "tool:probe", {})
    for _ in range(63):
        action = journal.tool_started("event", "loop", "tool:probe", {})
        journal.tool_finished(
            "event", "loop", action, returned_ok=True, observation_kind="handler_return"
        )
    calls = []
    with tool_execution_scope(trace, journal):
        result = execute_tool(
            "probe", {}, registry(lambda: calls.append(True) or {"ok": True})
        )
    assert result["execution_state"] == "not_started" and calls == []
    seal(journal)
    assert len(journal.get_by_event("event").actions) == 64


def test_return_observation_rolls_back_with_draft_write_failure(journal):
    begin(journal)
    action = journal.tool_started("event", "loop", "tool:probe", {})
    journal._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_UPDATE and table == "episode_processing"
        else sqlite3.SQLITE_OK
    )
    with pytest.raises(sqlite3.DatabaseError):
        journal.tool_finished(
            "event", "loop", action, returned_ok=True, observation_kind="handler_return"
        )
    assert journal.tool_receipts("event")[0].status == "started"
    journal._conn.set_authorizer(None)
    assert journal.tool_finished(
        "event", "loop", action, returned_ok=True, observation_kind="handler_return"
    )
    assert not journal.tool_finished(
        "event", "loop", action, returned_ok=False, observation_kind="handler_return"
    )
    assert journal.tool_receipts("event")[0].status == "completed"
