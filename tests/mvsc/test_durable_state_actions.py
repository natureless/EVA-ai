"""Atomic state/action commits, fenced dispatch and genuine process crash windows."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core.executor import FileExecutor
from packages.contracts.actions import ActionIntent, ActionReceipt, read_action_record
from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import ConsciousState, StateConflictError, StateDelta
from packages.contracts.protocols import TransactionalStateRepository
from packages.cognition.loop import PipelineCognitionLoop
from packages.kernel.action_dispatcher import ActionDispatcher
from packages.kernel.sqlite_state_repository import SQLiteStateRepository


@pytest.fixture
def repo(tmp_path):
    store = SQLiteStateRepository(tmp_path / "state.db")
    try:
        yield store
    finally:
        store.close()


def prepare(repo, tmp_path, *, event_id="source", action_id="action"):
    event = EventEnvelope(event_id=event_id, event_type="test.plan", source="test")
    state = repo.load_sync(event.subject_id)
    planned = state.apply(world_delta={"focus": "file write planned"})
    intent = ActionIntent(
        action_id=action_id,
        source_event_id=event.event_id,
        slot="write",
        tool_id="executor:file:write",
        parameters={"path": str(tmp_path / "effect.txt"), "content": "once"},
    )
    repo.commit_sync(state.version, planned, [event], [intent])
    return intent, planned, event


def file_handler(tmp_path, calls):
    executor = FileExecutor(
        SimpleNamespace(record=lambda **kwargs: None),
        {"executors": {"file": {"allowed_paths": [str(tmp_path)]}}},
    )

    async def handler(intent, state):
        calls.append(intent.action_id)
        return executor.execute("write", intent.parameters)

    return handler


@pytest.mark.asyncio
async def test_state_protocol_deltas_reopen_history_and_detached_reads(repo, tmp_path):
    assert isinstance(repo, TransactionalStateRepository)
    before = await repo.load("eva-001")
    event = EventEnvelope(event_type="test.input", source="test")
    assert (
        await repo.commit_delta(
            StateDelta(
                base_version=0, changes={"world": {"focus": "first"}}, source="test"
            ),
            [event],
        )
        == 1
    )
    candidate = (await repo.load("eva-001")).apply(world_delta={"focus": "second"})
    assert await repo.commit(1, candidate, []) == 2
    with pytest.raises(StateConflictError):
        await repo.commit_delta(StateDelta(base_version=0, source="stale"))
    history = await repo.history("eva-001", from_version=0, limit=1)
    assert history[0]["state"].version == 1
    history[0]["state"].world["mutated"] = True
    replayed = await repo.replay("eva-001", to_version=1)
    assert replayed[0].event_id == event.event_id
    replayed[0].payload["mutated"] = True
    assert (await repo.replay("eva-001"))[0].payload == {}
    assert before.version == 0 and before.world == {}
    reopened = SQLiteStateRepository(tmp_path / "state.db")
    try:
        assert (await reopened.load("eva-001")).model_dump() == candidate.model_dump()
        assert "mutated" not in (await reopened.history("eva-001"))[0]["state"].world
    finally:
        reopened.close()


def test_seed_is_detached_revision_zero_and_cannot_override_existing_authority(
    tmp_path,
):
    path = tmp_path / "seed.db"
    seed = ConsciousState(world={"focus": "seed"})
    repo = SQLiteStateRepository(path, initial_state=seed)
    try:
        seed.world["focus"] = "changed by caller"
        assert repo.load_sync("eva-001").world == {"focus": "seed"}
    finally:
        repo.close()
    reopened = SQLiteStateRepository(
        path, initial_state=ConsciousState(world={"focus": "replacement"})
    )
    try:
        assert reopened.load_sync("eva-001").world == {"focus": "seed"}
    finally:
        reopened.close()
    with pytest.raises(ValueError):
        SQLiteStateRepository(
            tmp_path / "bad.db", initial_state=ConsciousState(version=1)
        )


@pytest.mark.parametrize("invalid", ["hash", "revision", "nan", "oversized"])
def test_state_rejection_never_writes_or_queues_actions(repo, tmp_path, invalid):
    state = repo.load_sync("eva-001").apply()
    if invalid == "hash":
        state.world["changed"] = True
    elif invalid == "revision":
        state.version = True
    elif invalid == "nan":
        state.world["value"] = float("nan")
    else:
        state.world["large"] = "x" * 300_000
    with pytest.raises(ValueError):
        repo.commit_sync(0, state, [])
    assert repo.load_sync("eva-001").version == 0 and repo.pending_actions() == []


def test_wrong_subject_source_and_action_budget_are_rejected_before_commit(repo):
    state = repo.load_sync("eva-001").apply()
    wrong = EventEnvelope(
        event_id="wrong", subject_id="other", event_type="test", source="test"
    )
    with pytest.raises(ValueError):
        repo.commit_sync(0, state, [wrong])
    event = EventEnvelope(event_id="source", event_type="test", source="test")
    missing = ActionIntent(source_event_id="not-in-commit", slot="slot", tool_id="test")
    with pytest.raises(ValueError):
        repo.commit_sync(0, state, [event], [missing])
    too_many = [
        ActionIntent(source_event_id="source", slot=str(i), tool_id="test")
        for i in range(17)
    ]
    with pytest.raises(ValueError):
        repo.commit_sync(0, state, [event], too_many)
    assert repo.load_sync("eva-001").version == 0


@pytest.mark.asyncio
async def test_history_paging_preserves_whole_revisions_and_replay_refuses_silent_truncation(
    repo,
):
    for _ in range(101):
        state = repo.load_sync("eva-001")
        repo.commit_sync(state.version, state.apply(), [])
    first = await repo.history("eva-001", limit=100)
    rest = await repo.history(
        "eva-001", from_version=first[-1]["state"].version, limit=100
    )
    assert len(first) == 100 and [item["state"].version for item in rest] == [101]
    with pytest.raises(ValueError):
        await repo.replay("eva-001")
    assert await repo.replay("eva-001", to_version=100) == []
    for args in ({"from_version": True}, {"limit": 101}, {"from_version": -1}):
        with pytest.raises(ValueError):
            await repo.history("eva-001", **args)


@pytest.mark.parametrize(
    "table", ["cognitive_state_commits", "cognitive_states", "cognitive_action_outbox"]
)
def test_any_commit_insert_failure_rolls_back_every_table(repo, tmp_path, table):
    repo._conn.set_authorizer(
        lambda op, name, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and name == table
        else sqlite3.SQLITE_OK
    )
    with pytest.raises(sqlite3.Error):
        prepare(repo, tmp_path)
    repo._conn.set_authorizer(None)
    assert repo.load_sync("eva-001").version == 0
    assert repo.pending_actions() == []
    assert (
        repo._conn.execute("SELECT COUNT(*) FROM cognitive_state_commits").fetchone()[0]
        == 0
    )


def test_duplicate_action_or_source_slot_rolls_back_new_state(repo, tmp_path):
    intent, state, event = prepare(repo, tmp_path)
    for duplicate in (intent, intent.model_copy(update={"action_id": "another"})):
        with pytest.raises(sqlite3.IntegrityError):
            repo.commit_sync(1, state.apply(), [event], [duplicate])
    assert repo.load_sync("eva-001").version == 1
    assert len(repo.pending_actions()) == 1


def test_parallel_repository_instances_accept_only_one_base_revision(repo, tmp_path):
    other = SQLiteStateRepository(tmp_path / "state.db")
    state = repo.load_sync("eva-001")

    def commit(pair):
        store, value = pair
        try:
            return store.commit_sync(0, state.apply(world_delta={"winner": value}), [])
        except StateConflictError:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(commit, [(repo, "one"), (other, "two")]))
        assert results.count(1) == 1 and results.count("conflict") == 1
        assert repo.load_sync("eva-001").world["winner"] in {"one", "two"}
    finally:
        other.close()


@pytest.mark.asyncio
async def test_real_file_executor_dispatch_is_once_and_does_not_advance_state(
    repo, tmp_path
):
    intent, planned, _ = prepare(repo, tmp_path)
    calls = []
    handler = file_handler(tmp_path, calls)
    dispatcher = ActionDispatcher(repo)
    first = await dispatcher.dispatch(intent.action_id, handler)
    assert first["ok"] and (tmp_path / "effect.txt").read_text() == "once"
    assert calls == [intent.action_id]
    receipt = repo.action(intent.action_id)[1]
    assert (
        receipt.status == "completed" and receipt.observation_kind == "handler_return"
    )
    assert await repo.load("eva-001") == planned
    second = await dispatcher.dispatch(intent.action_id, handler)
    assert second["error"] == "action_already_claimed" and calls == [intent.action_id]
    assert repo.pending_actions() == []
    assert repo.recover_actions() == 0


@pytest.mark.asyncio
async def test_claim_write_failure_never_calls_executor(repo, tmp_path):
    intent, _, _ = prepare(repo, tmp_path)
    calls = []
    repo._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_UPDATE and table == "cognitive_action_outbox"
        else sqlite3.SQLITE_OK
    )
    result = await ActionDispatcher(repo).dispatch(
        intent.action_id, file_handler(tmp_path, calls)
    )
    repo._conn.set_authorizer(None)
    assert result["execution_state"] == "not_started" and calls == []
    assert repo.action(intent.action_id)[1].status == "pending"


@pytest.mark.asyncio
async def test_return_receipt_failure_never_retries_effect_and_recovery_is_unknown(
    repo, tmp_path, monkeypatch
):
    intent, _, _ = prepare(repo, tmp_path)
    calls = []

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected failure")

    monkeypatch.setattr(repo, "finish_action", fail)
    result = await ActionDispatcher(repo).dispatch(
        intent.action_id, file_handler(tmp_path, calls)
    )
    assert result["execution_state"] == "outcome_unknown" and calls == [
        intent.action_id
    ]
    assert (tmp_path / "effect.txt").read_text() == "once"
    assert repo.recover_actions() == 1
    restored = repo.action(intent.action_id)[1]
    assert (
        restored.status == "unknown"
        and restored.finished_at is None
        and restored.returned_ok is None
    )
    assert restored.sealed_at is not None
    assert (
        await ActionDispatcher(repo).dispatch(
            intent.action_id, file_handler(tmp_path, calls)
        )
    )["error"] == "action_already_claimed"
    assert calls == [intent.action_id]


@pytest.mark.parametrize("throws", [False, True])
@pytest.mark.asyncio
async def test_failed_or_exception_return_stays_unknown_after_actual_effect(
    repo, tmp_path, throws
):
    intent, _, _ = prepare(repo, tmp_path)

    async def handler(intent, state):
        (tmp_path / "effect.txt").write_text("happened")
        if throws:
            raise RuntimeError("after effect")
        return {"ok": False}

    if throws:
        with pytest.raises(RuntimeError):
            await ActionDispatcher(repo).dispatch(intent.action_id, handler)
    else:
        assert not (await ActionDispatcher(repo).dispatch(intent.action_id, handler))[
            "ok"
        ]
    assert (tmp_path / "effect.txt").read_text() == "happened"
    assert repo.action(intent.action_id)[1].status == "unknown"


def test_claim_token_and_first_unknown_terminal_are_immutable(repo, tmp_path):
    intent, _, _ = prepare(repo, tmp_path)
    _, claimed, _ = repo.claim_action(intent.action_id)
    with pytest.raises(ValueError):
        repo.finish_action(intent.action_id, "wrong", returned_ok=True)
    assert repo.recover_actions() == 1
    assert not repo.finish_action(
        intent.action_id, claimed.claim_token, returned_ok=True
    )
    assert repo.action(intent.action_id)[1].status == "unknown"
    assert repo.claim_action(intent.action_id) is None


@pytest.mark.asyncio
async def test_real_dispatch_occupancy_blocks_close_and_recovery(repo, tmp_path):
    intent, _, _ = prepare(repo, tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(intent, state):
        assert not repo._conn.in_transaction
        started.set()
        await release.wait()
        return {"ok": True}

    task = asyncio.create_task(
        ActionDispatcher(repo).dispatch(intent.action_id, handler)
    )
    await asyncio.wait_for(started.wait(), 5)
    try:
        with pytest.raises(RuntimeError):
            repo.close()
        with pytest.raises(RuntimeError):
            repo.recover_actions()
    finally:
        release.set()
        assert (await task)["ok"]


@pytest.mark.asyncio
async def test_two_dispatchers_cannot_execute_same_claim(repo, tmp_path):
    intent, _, _ = prepare(repo, tmp_path)
    second = SQLiteStateRepository(tmp_path / "state.db")
    calls = []
    try:
        results = await asyncio.gather(
            ActionDispatcher(repo).dispatch(
                intent.action_id, file_handler(tmp_path, calls)
            ),
            ActionDispatcher(second).dispatch(
                intent.action_id, file_handler(tmp_path, calls)
            ),
        )
        assert sum(result.get("ok") is True for result in results) == 1
        assert calls == [intent.action_id]
    finally:
        second.close()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"status": "completed"},
        {"returned_ok": True},
        {"state_version": True},
        {"claim_token": "fake"},
    ],
)
def test_receipt_codec_rejects_unproven_claims(repo, tmp_path, change):
    intent, _, _ = prepare(repo, tmp_path)
    receipt = repo.action(intent.action_id)[1].model_dump(mode="json")
    with pytest.raises(ValueError):
        read_action_record(json.dumps({**receipt, **change}), ActionReceipt)


def test_corrupt_state_or_action_binding_blocks_dispatch_instead_of_defaulting(
    repo, tmp_path
):
    intent, planned, _ = prepare(repo, tmp_path)
    repo._conn.execute("UPDATE cognitive_action_outbox SET source_event_id='forged'")
    repo._conn.commit()
    with pytest.raises(ValueError):
        repo.claim_action(intent.action_id)
    repo._conn.execute(
        "UPDATE cognitive_states SET state_json=?",
        (json.dumps({**planned.model_dump(mode="json"), "world": {"forged": True}}),),
    )
    repo._conn.commit()
    with pytest.raises(ValueError):
        repo.load_sync("eva-001")


@pytest.mark.parametrize("window", ["pending", "claimed", "effect"])
def test_real_process_exit_windows_preserve_state_intent_and_never_replay_claim(
    tmp_path, window
):
    script = """
import asyncio,os,sys
from pathlib import Path
from types import SimpleNamespace
from core.executor import FileExecutor
from packages.contracts.actions import ActionIntent
from packages.contracts.events import EventEnvelope
from packages.kernel.sqlite_state_repository import SQLiteStateRepository
root=Path(sys.argv[1]);repo=SQLiteStateRepository(root/"state.db")
event=EventEnvelope(event_id="source",event_type="test",source="test")
intent=ActionIntent(action_id="action",source_event_id="source",slot="write",tool_id="executor:file:write",parameters={"path":str(root/"effect.txt"),"content":"once"})
repo.commit_sync(0,repo.load_sync("eva-001").apply(world_delta={"planned":True}),[event],[intent])
if sys.argv[2]=="pending":os._exit(49)
repo.claim_action("action")
if sys.argv[2]=="claimed":os._exit(49)
executor=FileExecutor(SimpleNamespace(record=lambda **kw:None),{"executors":{"file":{"allowed_paths":[str(root)]}}})
executor.execute("write",intent.parameters)
os._exit(49)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), window],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 49, result.stderr.decode(errors="replace")
    repo = SQLiteStateRepository(tmp_path / "state.db")
    try:
        assert repo.load_sync("eva-001").world == {"planned": True}
        assert repo.recover_actions() == (0 if window == "pending" else 1)
        calls = []
        reply = asyncio.run(
            ActionDispatcher(repo).dispatch("action", file_handler(tmp_path, calls))
        )
        if window == "pending":
            assert reply["ok"] and calls == ["action"]
        else:
            assert reply["execution_state"] == "not_started" and calls == []
            assert repo.action("action")[1].status == "unknown"
        assert (tmp_path / "effect.txt").exists() == (window != "claimed")
    finally:
        repo.close()


class FileDecision:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    async def decide(self, state, evaluation):
        return {
            "requires_action": True,
            "intent": {
                "path": str(self.tmp_path / "pipeline.txt"),
                "content": "pipeline-once",
            },
        }


class FileLoop(PipelineCognitionLoop):
    def __init__(self, repo, tmp_path):
        super().__init__(
            state_repo=repo,
            action_dispatcher=ActionDispatcher(repo),
            decision_engine=FileDecision(tmp_path),
            agent_os=object(),
        )
        self.calls = 0
        self.executor = FileExecutor(
            SimpleNamespace(record=lambda **kw: None),
            {"executors": {"file": {"allowed_paths": [str(tmp_path)]}}},
        )

    async def _update_world(self, state, event):
        return {"focus": event.payload["text"]}

    async def _execute_action(self, state, decision, plan):
        self.calls += 1
        # The action's planned world state already exists durably before this call.
        assert self.state_repo.load_sync(state.subject_id).version >= state.version
        assert state.world["focus"] == "planned focus"
        assert not self.state_repo._conn.in_transaction
        result = self.executor.execute("write", decision["intent"])
        return [
            EventEnvelope(
                event_type=EventFamily.ACTION.TOOL_COMPLETED,
                source="test",
                payload={"ok": result["ok"]},
            )
        ]


@pytest.mark.asyncio
async def test_existing_pipeline_commits_before_action_and_feedback_does_not_double_tick(
    repo, tmp_path
):
    loop = FileLoop(repo, tmp_path)
    event = EventEnvelope(
        event_id="source",
        event_type=EventFamily.PERCEPTION.USER_MESSAGE,
        source="test",
        payload={"text": "planned focus"},
    )
    final = await loop.run_once(event)
    assert final.version == 2 and final.tick == 1
    assert (
        loop.calls == 1 and (tmp_path / "pipeline.txt").read_text() == "pipeline-once"
    )
    assert (
        repo.action_for_source("eva-001", "source", "cognition_act")[1].status
        == "completed"
    )
    assert (await loop.run_once(event)).version == 2 and loop.calls == 1
    assert [item["state"].version for item in await repo.history("eva-001")] == [1, 2]


@pytest.mark.asyncio
async def test_pipeline_feedback_commit_failure_does_not_repeat_action(
    repo, tmp_path, monkeypatch
):
    loop = FileLoop(repo, tmp_path)
    event = EventEnvelope(
        event_id="source",
        event_type=EventFamily.PERCEPTION.USER_MESSAGE,
        source="test",
        payload={"text": "planned focus"},
    )
    original = repo.commit

    async def fail(*args, **kwargs):
        raise sqlite3.OperationalError("feedback commit failure")

    monkeypatch.setattr(repo, "commit", fail)
    with pytest.raises(sqlite3.Error):
        await loop.run_once(event)
    assert repo.load_sync("eva-001").version == 1
    assert (
        repo.action_for_source("eva-001", "source", "cognition_act")[1].status
        == "completed"
    )
    monkeypatch.setattr(repo, "commit", original)
    assert (await loop.run_once(event)).version == 1 and loop.calls == 1


@pytest.mark.asyncio
async def test_pipeline_rejects_different_source_content_under_used_event_id(
    repo, tmp_path
):
    loop = FileLoop(repo, tmp_path)
    event = EventEnvelope(
        event_id="source",
        event_type=EventFamily.PERCEPTION.USER_MESSAGE,
        source="test",
        payload={"text": "planned focus"},
    )
    await loop.run_once(event)
    conflicting = event.model_copy(deep=True)
    conflicting.payload["text"] = "different request"
    with pytest.raises(ValueError):
        await loop.run_once(conflicting)
    assert loop.calls == 1


@pytest.mark.asyncio
async def test_pipeline_pending_intent_can_be_explicitly_dispatched_after_failed_claim(
    repo, tmp_path
):
    loop = FileLoop(repo, tmp_path)
    event = EventEnvelope(
        event_id="source",
        event_type=EventFamily.PERCEPTION.USER_MESSAGE,
        source="test",
        payload={"text": "planned focus"},
    )
    repo._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_UPDATE and table == "cognitive_action_outbox"
        else sqlite3.SQLITE_OK
    )
    final = await loop.run_once(event)
    assert final.version == 2 and loop.calls == 0
    repo._conn.set_authorizer(None)
    pending = repo.pending_actions()[0]
    result = await loop.dispatch_pending_action(pending.action_id)
    assert result["ok"] and loop.calls == 1
    assert (await repo.load("eva-001")).version == 2
