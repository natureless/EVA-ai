"""Actual context revisions, atomic agent intent and immutable recovery evidence."""

from datetime import datetime, timedelta, timezone
import json
import importlib
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agents.base_agent import AgentTask
from app.config import Settings
from core.context_builder import ContextBuilder
from event.event_schema import Event
from memory.context_evidence import action_context_evidence, context_hash
from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from memory.versioned_reads import install_memory_revisions, revision_row
from packages.kernel.request_dispatch_store import RequestDispatchStore
from runtime.request_persistence import ActionContextError
from runtime.result_registry import ResultRegistry
from world.world_model import WorldModelGraph


def wait_idle(container):
    deadline = time.monotonic() + 5
    while not container.runtime.processor.is_idle and time.monotonic() < deadline:
        time.sleep(0.01)
    assert container.runtime.processor.is_idle


@pytest.fixture
def memory_store(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    store.init_db()
    install_memory_revisions(store)
    yield store
    store.close()


def test_memory_revisions_follow_raw_writes_and_transaction_rollback(memory_store):
    tiered = TieredMemoryManager(memory_store)
    start = revision_row(memory_store)
    mid = tiered.s2.put("remember this", memory_id="working")
    assert revision_row(memory_store)["revision"] == start["revision"] + 1
    conn = memory_store._get_conn()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE working_memory SET content='temporary' WHERE id=?", (mid,))
    assert revision_row(memory_store)["revision"] == start["revision"] + 2
    conn.rollback()
    assert revision_row(memory_store)["revision"] == start["revision"] + 1
    assert tiered.s2.get(mid)["content"] == "remember this"
    memory_store.execute("DELETE FROM working_memory WHERE id=?", (mid,))
    assert revision_row(memory_store)["revision"] == start["revision"] + 2
    tiered.s3.put("long term", memory_id="long")
    tiered.s3.archive("long")
    assert revision_row(memory_store)["revision"] == start["revision"] + 4


def test_memory_scope_survives_reopen_and_install_is_idempotent(memory_store):
    before = revision_row(memory_store)
    install_memory_revisions(memory_store)
    another = SQLiteStore(memory_store.db_path)
    try:
        install_memory_revisions(another)
        assert revision_row(another) == before
    finally:
        another.close()


def test_recall_pins_rows_and_revision_across_real_concurrent_writer(
    memory_store, monkeypatch
):
    tiered = TieredMemoryManager(memory_store)
    tiered.s2.put("old needle", memory_id="item")
    before = revision_row(memory_store)
    original = tiered.recall

    def write():
        memory_store.execute(
            "UPDATE working_memory SET content='new needle' WHERE id='item'"
        )

    def recall(query, tiers):
        thread = threading.Thread(target=write)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        return original(query, tiers)

    monkeypatch.setattr(tiered, "recall", recall)
    snapshot = tiered.recall_snapshot("needle")
    assert snapshot["reference"]["revision"] == before["revision"]
    assert snapshot["memories"][0]["content"] == "old needle"
    assert revision_row(memory_store)["revision"] == before["revision"] + 1
    assert tiered.s2.get("item")["content"] == "new needle"
    assert snapshot["reference"]["integrity_hash"] == context_hash(snapshot["memories"])


def test_read_snapshot_rejects_writes_nesting_and_restores_connection(memory_store):
    with memory_store.read_snapshot():
        revision_row(memory_store)
        with pytest.raises(RuntimeError):
            memory_store.execute("DELETE FROM working_memory")
        with pytest.raises(RuntimeError):
            with memory_store.read_snapshot():
                pass
    tiered = TieredMemoryManager(memory_store)
    tiered.s2.put("after snapshot")
    assert not memory_store._get_conn().in_transaction
    assert memory_store._get_conn().execute("PRAGMA query_only").fetchone()[0] == 0


def test_world_revision_tracks_top_level_and_graph_mutations_without_live_aliases():
    model = WorldModelGraph()
    before = model.context_projection()["reference"]
    model.focus = "new focus"
    changed = model.context_projection()["reference"]
    assert changed["revision"] > before["revision"]
    model.focus = "new focus"
    assert model.context_projection()["reference"] == changed
    eid = model.upsert_entity("task", "Test", {"status": "active"})
    edge = model.link(eid, "user", "owns")
    actual = model.context_projection()
    assert actual["reference"]["revision"] > changed["revision"]
    edge.weight = 100
    model.get_entity(eid).properties["status"] = "completed"
    model.get_edges()[0].weight = -100
    model.get_entities_by_type("task")[0].name = "replaced"
    assert model.context_projection() == actual
    assert model.get_edges()[0].weight == 1
    assert actual["reference"]["integrity_hash"] == context_hash(
        {key: value for key, value in actual.items() if key != "reference"}
    )
    restored = WorldModelGraph.from_dict(model.to_dict())
    assert (
        restored.context_projection()["reference"]["scope"]
        != actual["reference"]["scope"]
    )


@pytest.fixture
def rig(memory_store, tmp_path):
    tiered = TieredMemoryManager(memory_store)
    tiered.versioned_context_reads = True
    tiered.s2.put("Hello memory", memory_id="context-memory")
    world = WorldModelGraph()
    world.upsert_entity("task", "Hello task")
    context = ContextBuilder(tiered_memory=tiered, world_model=world).build(
        user_id="default", text="Hello"
    )
    task = AgentTask("chat", {"text": "Hello", "context": context})
    clock = SimpleNamespace(now=datetime(2026, 10, 1, tzinfo=timezone.utc), mono=100.0)
    ledger = RequestDispatchStore(
        tmp_path / "ledger.db", enable_action_context=True, clock=lambda: clock.now
    )
    registry = ResultRegistry(
        persistence=ledger,
        clock=lambda: clock.mono,
        pending_timeout_sec=10,
        retention_sec=1,
    )
    event = Event(
        id="input-event",
        type="user_message",
        source="user",
        correlation_id="request-task",
        payload={"text": "Hello"},
    )
    registry.create(event.correlation_id, event_id=event.id, event=event)
    assert (
        registry.begin(event.correlation_id, event_id=event.id, event=event)
        == "started"
    )
    yield ledger, registry, event, task, world, clock
    ledger.end_processing(event.correlation_id, event.id, returned_ok=None)
    ledger.close()


def test_agent_intent_and_context_are_atomic_immutable_and_bound_to_parent(rig):
    ledger, registry, event, task, world, _ = rig
    expected = action_context_evidence(task).model_dump(mode="json")
    binding = registry.begin_agent_action(event, "chat_agent", task)
    view = ledger.agent_context_for_task(event.correlation_id)
    assert view["inputs"] == expected
    assert view["inputs"]["memory_items"][0]["memory_id"] == "context-memory"
    assert view["intent"]["state_version"] == 2
    assert view["parent_action_id"] == ledger._record(event.correlation_id).action_id
    assert "claim_token" not in view["observation"]
    world.apply_user_message("later")
    task.payload["context"]["memories"][0]["content"] = "changed after capture"
    assert ledger.agent_context_for_task(event.correlation_id)["inputs"] == expected
    with pytest.raises(ActionContextError):
        registry.begin_agent_action(event, "chat_agent", task)
    registry.finish_agent_action(event, binding, returned_ok=True)
    assert (
        ledger.agent_context_for_task(event.correlation_id)["observation"]["status"]
        == "completed"
    )


def test_intent_insert_failure_rolls_back_state_and_blocks_invocation(rig):
    ledger, registry, event, task, _, _ = rig
    ledger._conn.execute("""CREATE TRIGGER reject_agent BEFORE INSERT ON cognitive_action_outbox
        WHEN NEW.slot='agent_invocation' BEGIN SELECT RAISE(ABORT,'injected'); END""")
    with pytest.raises(ActionContextError):
        registry.begin_agent_action(event, "chat_agent", task)
    assert ledger.load_sync(ledger.subject_id).version == 1
    assert ledger.agent_context_for_task(event.correlation_id) is None
    assert len(ledger._history(ledger.subject_id, 0, None, 100)) == 1


def test_original_deadline_and_missing_cache_never_bypass_agent_gate(rig):
    ledger, registry, event, task, _, clock = rig
    clock.now += timedelta(seconds=12)
    clock.mono += 12
    with pytest.raises(ActionContextError):
        registry.begin_agent_action(event, "chat_agent", task)
    clock.mono += 2
    registry.cleanup(ttl_sec=1)
    with pytest.raises(ActionContextError):
        registry.begin_agent_action(event, "chat_agent", task)
    assert ledger.agent_context_for_task(event.correlation_id) is None


def test_action_context_requires_durable_settings():
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, enable_action_context=True, enable_durable_requests=False
        )


@pytest.mark.parametrize("window", ["intent", "effect", "receipt"])
def test_real_process_exit_preserves_context_and_never_replays_agent(tmp_path, window):
    db_path = tmp_path / "crash.db"
    effect = tmp_path / "effect.txt"
    script = """
import os, sys
from agents.base_agent import AgentTask
from core.context_builder import ContextBuilder
from event.event_schema import Event
from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from memory.versioned_reads import install_memory_revisions
from packages.kernel.request_dispatch_store import RequestDispatchStore
from runtime.result_registry import ResultRegistry
from world.world_model import WorldModelGraph
memory = SQLiteStore(__import__('pathlib').Path(sys.argv[1]))
memory.init_db()
install_memory_revisions(memory)
tiered = TieredMemoryManager(memory)
tiered.versioned_context_reads = True
tiered.s2.put('crash memory', memory_id='original')
context = ContextBuilder(tiered_memory=tiered, world_model=WorldModelGraph()).build(user_id='default', text='crash')
ledger = RequestDispatchStore(sys.argv[1], enable_action_context=True)
registry = ResultRegistry(persistence=ledger)
event = Event(id='crash-event', type='user_message', source='user', correlation_id='crash-task', payload={'text':'crash'})
registry.create(event.correlation_id, event_id=event.id, event=event)
assert registry.begin(event.correlation_id, event_id=event.id, event=event) == 'started'
binding = registry.begin_agent_action(event, 'chat_agent', AgentTask('chat', {'text':'crash', 'context':context}))
if sys.argv[3] != 'intent':
    with open(sys.argv[2], 'a', encoding='utf-8') as output:
        output.write('effect\\n')
if sys.argv[3] == 'receipt':
    registry.finish_agent_action(event, binding, returned_ok=True)
os._exit(49)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(effect), window],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 49, completed.stderr.decode()
    ledger = RequestDispatchStore(db_path, enable_action_context=True)
    try:
        before = ledger.agent_context_for_task("crash-task")
        assert before["inputs"]["memory_items"][0]["memory_id"] == "original"
        while ledger.recover():
            pass
        after = ledger.agent_context_for_task("crash-task")
        assert after["inputs"] == before["inputs"]
        assert after["intent"] == before["intent"]
        assert after["observation"]["status"] == (
            "completed" if window == "receipt" else "unknown"
        )
        assert after["external_actions_replayed"] is False
        assert (
            ledger.lookup("crash-task")["payload"]["terminal_state"]
            == "outcome_unknown"
        )
        assert (effect.read_text() if effect.exists() else "") == (
            "" if window == "intent" else "effect\n"
        )
        ledger.prepare_resume()
        assert ledger.resume_page()["events"] == []
    finally:
        ledger.close()


@pytest.fixture(params=["legacy", "minimal"])
def context_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_DURABLE_REQUESTS", "true")
    monkeypatch.setenv("EVA_ENABLE_ACTION_CONTEXT", "true")
    monkeypatch.setenv("EVA_ENABLE_PROCESSING_EPISODES", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def test_actual_chat_context_matches_worker_task_and_episode(
    context_client, monkeypatch
):
    client = context_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    processor = container.runtime.processor
    actual = []
    original = processor._execute_agent

    def execute(agent, task, *args):
        view = ledger.agent_context_for_task(task.trace_context["correlation_id"])
        assert view["observation"]["status"] == "executing"
        assert view["inputs"] == action_context_evidence(task).model_dump(mode="json")
        actual.append(view)
        return original(agent, task, *args)

    monkeypatch.setattr(processor, "_execute_agent", execute)
    response = client.post(
        "/api/chat/sync", json={"text": "Hello", "remember": True, "mode": "deep"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert len(actual) == 1
    view = client.get(f"/api/action-context/{payload['task_id']}").json()
    assert view["observation"]["status"] == "completed"
    episode = container.integrations.processing_episodes.get_by_event(view["event_id"])
    assert episode.actions[0].action_id == view["intent"]["action_id"]
    assert (
        episode.metadata["agent_intent"]["state_version"]
        == view["intent"]["state_version"]
    )
    assert "claim_token" not in json.dumps(view)
    assert "Hello" not in json.dumps(view)
    assert client.get("/api/action-context/missing").status_code == 404
    assert (
        client.get(
            f"/api/action-context/{payload['task_id']}",
            headers={"X-API-Token": "invalid"},
        ).status_code
        == 403
    )


def test_actual_commit_failure_prevents_worker_call(context_client, monkeypatch):
    client = context_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    called = []
    monkeypatch.setattr(
        container.runtime.processor, "_execute_agent", lambda *args: called.append(True)
    )
    ledger._conn.execute("""CREATE TRIGGER fail_agent BEFORE INSERT ON cognitive_action_outbox
        WHEN NEW.slot='agent_invocation' BEGIN SELECT RAISE(ABORT,'injected'); END""")
    payload = client.post("/api/chat/sync", json={"text": "Hello"}).json()
    assert payload["error"] == "action_context_unavailable"
    assert payload["terminal_state"] == "rejected"
    assert called == []


def test_input_graph_uses_saved_references_and_only_reads(context_client):
    client = context_client
    container = client.app.state.container
    container.memory.tiered.s2.put("needle original", memory_id="graph-input")
    payload = client.post("/api/chat/sync", json={"text": "needle"}).json()
    wait_idle(container)
    task_id = payload["task_id"]
    view = client.get(f"/api/action-context/{task_id}").json()
    endpoint = f"/api/action-context/{task_id}/graph"
    params = {"event_id": view["event_id"]}
    before = container.integrations.durable_requests._conn.total_changes
    response = client.get(endpoint, params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    graph = response.json()
    assert graph["binding"]["action_id"] == view["intent"]["action_id"]
    assert graph["scope"]["read_only"] is True
    assert graph["scope"]["external_actions_replayed"] is False
    assert all(n["tier"] in {"A", "I"} for n in graph["nodes"])
    assert all(
        e["source"] == "A:" + view["intent"]["action_id"] for e in graph["edges"]
    )
    assert "needle original" not in json.dumps(graph)
    assert "claim_token" not in json.dumps(graph)
    assert container.integrations.durable_requests._conn.total_changes == before
    container.memory.store.execute(
        "UPDATE working_memory SET content='changed' WHERE id='graph-input'"
    )
    again = client.get(endpoint, params=params).json()
    assert again["nodes"] == graph["nodes"]
    assert again["edges"] == graph["edges"]
    assert client.get(endpoint).status_code == 422
    assert client.get(endpoint, params={"event_id": "wrong"}).status_code == 409
    assert (
        client.get(endpoint, params=params, headers={"X-API-Token": "bad"}).status_code
        == 403
    )
    assert (
        client.get("/api/action-context/missing/graph", params=params).status_code
        == 404
    )


def test_after_call_receipt_failure_is_unknown_and_not_reexecuted(
    context_client, monkeypatch
):
    client = context_client
    container = client.app.state.container
    ledger = container.integrations.durable_requests
    called = []
    original = container.runtime.processor._execute_agent

    def execute(*args):
        called.append(True)
        return original(*args)

    monkeypatch.setattr(container.runtime.processor, "_execute_agent", execute)
    monkeypatch.setattr(
        ledger,
        "finish_agent_action",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    payload = client.post("/api/chat/sync", json={"text": "Hello"}).json()
    assert payload["terminal_state"] == "outcome_unknown"
    assert called == [True]
    wait_idle(container)
    view = ledger.agent_context_for_task(payload["task_id"])
    assert view["observation"]["status"] == "unknown"
    event = container.memory.api.get_event(view["event_id"])
    container.runtime.processor.process_event(event)
    assert called == [True]


def test_streaming_also_commits_intent_before_actual_worker(
    context_client, monkeypatch
):
    client = context_client
    container = client.app.state.container
    actual = []
    original = container.runtime.processor._execute_agent_stream

    def execute(agent, task, task_id):
        view = container.integrations.durable_requests.agent_context_for_task(task_id)
        assert view["inputs"] == action_context_evidence(task).model_dump(mode="json")
        actual.append(view)
        return original(agent, task, task_id)

    monkeypatch.setattr(container.runtime.processor, "_execute_agent_stream", execute)
    response = client.post("/api/chat/stream", json={"text": "Hello"})
    assert response.status_code == 200
    assert len(actual) == 1
    view = client.get(f"/api/action-context/{actual[0]['task_id']}").json()
    assert view["observation"]["status"] == "completed"


def test_context_read_survives_real_bootstrap_restart_without_new_action(
    context_client,
):
    client = context_client
    old = client.app.state.container
    result = client.post("/api/chat/sync", json={"text": "Hello"}).json()
    wait_idle(old)
    before = client.get(f"/api/action-context/{result['task_id']}").json()
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(old)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        assert client.get(f"/api/action-context/{result['task_id']}").json() == before
        assert (
            restarted.world.model.context_projection()["reference"]["scope"]
            != before["inputs"]["world"]["scope"]
        )
        assert (
            revision_row(restarted.memory.store)["scope"]
            == before["inputs"]["memory"]["scope"]
        )
        assert restarted.integrations.durable_requests._active_dispatches == 0
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = old


def test_default_has_no_revision_schema_or_action_context_registration(client):
    container = client.app.state.container
    assert container.settings.enable_action_context is False
    assert (
        container.memory.store.fetchone(
            "SELECT name FROM sqlite_master WHERE name='memory_view_revision'"
        )
        is None
    )
    assert client.get("/api/action-context/missing").status_code == 503
    assert (
        client.get(
            "/api/action-context/missing/graph", params={"event_id": "event"}
        ).status_code
        == 503
    )
