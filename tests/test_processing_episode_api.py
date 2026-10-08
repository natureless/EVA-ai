"""Opt-in processor recording shares the EVA DB under either consumer."""

import importlib
from threading import Event
import sqlite3
import time

import pytest
from core.tool_registry import ToolDef


@pytest.fixture(params=["legacy", "minimal"])
def episode_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_PROCESSING_EPISODES", "true")
    monkeypatch.setenv("EVA_ENABLE_BUSINESS_GOALS", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def goal_body(task_id):
    return {
        "task_id": task_id,
        "description": "追溯自动记录",
        "success_conditions": [{"field": "checks.ready", "expected": True}],
    }


def test_actual_runtime_records_goal_reference_and_survives_restart(episode_client):
    client = episode_client
    old = client.app.state.container
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    assert old.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    saved = old.integrations.processing_episodes.get_by_event(queued["event_id"])
    assert saved.schema_version == 2 and saved.result.status == "succeeded"
    assert saved.state_before.version is None and saved.state_after.version is None
    assert saved.metadata["task_id"] == queued["task_id"]
    registered = client.post("/api/goals", json=goal_body(queued["task_id"])).json()
    endpoint = f"/api/goals/{registered['goal_id']}/episode"
    response = client.get(endpoint)
    assert (
        response.status_code == 200
        and response.json()["episode"]["episode_id"] == saved.episode_id
    )
    assert (
        response.json()["episode"]["state_reference_kind"]
        == "world_snapshot_unversioned"
    )
    assert registered["status"] == "pending_verification"
    graph = client.get("/api/goals/graph").json()
    assert graph["counts"]["E"]["total"] == 1
    state = client.get("/api/runtime").json()
    assert state["requested"]["enable_processing_episodes"] is True
    assert state["extensions"]["processing_episodes"]["attached"] is True
    trace_count = old.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(old)
    assert old.integrations.processing_episodes._closed
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        restored = client.get(endpoint).json()["episode"]
        assert restored == response.json()["episode"]
        assert restarted.integrations.processing_episodes.stats()["processing"] == 0
        assert (
            restarted.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
            == trace_count
        )
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = old


def test_episode_observer_failure_does_not_skip_goal_receipt_or_replace_reply(
    episode_client, monkeypatch
):
    client = episode_client
    container = client.app.state.container
    journal = container.integrations.processing_episodes
    release = Event()
    original = container.runtime.processor._process_one

    def gated(*args, **kwargs):
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(container.runtime.processor, "_process_one", gated)

    def fail(*args, **kwargs):
        raise RuntimeError("injected recording failure")

    monkeypatch.setattr(journal, "record_receipt", fail)
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    try:
        created = client.post("/api/goals", json=goal_body(queued["task_id"])).json()
    finally:
        release.set()
    receipt = container.runtime.results.wait(queued["task_id"], timeout=10)
    assert receipt["ok"] and receipt["terminal_state"] == "succeeded"
    stored = container.integrations.business_goals.get(created["goal_id"])
    assert stored["status"] == "pending_verification"
    assert stored["processing_receipt"]["terminal_state"] == "succeeded"
    assert journal.get_by_event(queued["event_id"]) is None
    assert container.runtime.results.stats()["receipt_observer_errors"] >= 1
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(container)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        recovered = restarted.integrations.processing_episodes.get_by_event(
            queued["event_id"]
        )
        assert recovered.result.status == "succeeded"
        assert recovered.metadata["receipt_recovered"] is True
        assert recovered.metadata["completion_kind"] == "canonical_receipt"
        assert (
            restarted.integrations.processing_episodes.stats()[
                "receipt_notifications_pending"
            ]
            == 0
        )
        restored = client.get(f"/api/goals/{created['goal_id']}")
        assert (
            restored.status_code == 200
            and restored.json()["processing_receipt"]["ok"] is True
        )
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = container


def test_default_runtime_does_not_construct_or_create_processing_store(client):
    container = client.app.state.container
    assert container.integrations.processing_episodes is None
    assert container.runtime.processor.episode_recorder is None
    assert (
        container.store.fetchone(
            "SELECT name FROM sqlite_master WHERE name='episode_processing'"
        )
        is None
    )
    defaults = client.get("/api/config/defaults").json()["defaults"]
    assert defaults["enable_processing_episodes"] is False


def test_goal_projection_outbox_is_delivered_before_restart_goal_recovery(
    episode_client, monkeypatch
):
    client = episode_client
    old = client.app.state.container
    goals = old.integrations.business_goals
    journal = old.integrations.processing_episodes
    release = Event()
    original = old.runtime.processor._process_one

    def gated(*args, **kwargs):
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(old.runtime.processor, "_process_one", gated)
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    try:
        created = client.post("/api/goals", json=goal_body(queued["task_id"])).json()
        goals._conn.set_authorizer(
            lambda op, table, *args: sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_INSERT and table == "business_goal_receipts"
            else sqlite3.SQLITE_OK
        )
    finally:
        release.set()
    receipt = old.runtime.results.wait(queued["task_id"], timeout=10)
    assert receipt["ok"] and receipt["terminal_state"] == "succeeded"
    deadline = time.monotonic() + 5
    while not old.runtime.processor.is_idle and time.monotonic() < deadline:
        time.sleep(0.01)
    assert old.runtime.processor.is_idle
    assert journal.stats()["receipt_notifications_pending"] == 1
    assert goals.get(created["goal_id"])["processing_receipt"] is None
    assert journal.get_by_event(queued["event_id"]).result.status == "succeeded"
    public = client.get("/api/runtime").json()["extensions"]["processing_episodes"]
    assert public["receipt_notifications_pending"] == 1
    assert public["receipt_delivery_errors"] >= 1
    trace_count = old.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
    goals._conn.set_authorizer(None)
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(old)
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        restored = client.get(f"/api/goals/{created['goal_id']}").json()
        assert restored["processing_receipt"]["terminal_state"] == "succeeded"
        # Success remains unverified, and restart retains the interruption policy.
        assert restored["status"] == "interrupted"
        assert (
            restarted.integrations.processing_episodes.stats()[
                "receipt_notifications_pending"
            ]
            == 0
        )
        assert (
            restarted.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
            == trace_count
        )
        assert client.get(f"/api/chat/result/{queued['task_id']}").status_code == 404
    finally:
        assert bootstrap.shutdown_system(restarted)
        client.app.state.container = old


def test_existing_goal_reconciles_compact_receipt_after_registry_removal(
    episode_client, monkeypatch
):
    client = episode_client
    container = client.app.state.container
    journal = container.integrations.processing_episodes
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    assert container.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    created = client.post("/api/goals", json=goal_body(queued["task_id"])).json()
    container.runtime.results.pop(queued["task_id"])
    assert client.get(f"/api/chat/result/{queued['task_id']}").status_code == 404
    calls = []
    original = journal.receipt_for_task

    def observed(task_id):
        calls.append(task_id)
        return original(task_id)

    monkeypatch.setattr(journal, "receipt_for_task", observed)
    endpoint = f"/api/goals/{created['goal_id']}"
    assert (
        client.get(endpoint).json()["processing_receipt"]["terminal_state"]
        == "succeeded"
    )
    assert calls == [queued["task_id"]]
    monkeypatch.setattr(
        journal,
        "receipt_for_task",
        lambda *a: (_ for _ in ()).throw(ValueError("injected corruption")),
    )
    assert client.get(endpoint).status_code == 503


@pytest.mark.parametrize("streaming", [False, True])
def test_actual_chatagent_records_nested_tool_executor_receipts(
    episode_client, monkeypatch, tmp_path, streaming
):
    client = episode_client
    container = client.app.state.container
    journal = container.integrations.processing_episodes
    effect = tmp_path / "tool-effect.txt"
    calls = []

    def probe():
        calls.append(True)
        return container.runtime.executors["file"].execute(
            "write",
            {"path": str(effect), "content": "private-tool-content"},
            task_id="not-the-server-event",
        )

    container.agents.tool_registry.register(
        ToolDef("receipt_probe", "test fixture", {}, probe)
    )

    class ScriptedLLM:
        provider = "test"

        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            self.calls += 1
            if self.calls == 1:
                return '```tool\n{"tool":"receipt_probe","args":{}}\n```'
            return '```eva_response\n{"message":"处理器已返回。","claims":[]}\n```'

        def chat_stream(self, messages):
            yield self.chat(messages)

    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: ScriptedLLM())
    if streaming:
        response = client.post("/api/chat/stream", json={"text": "Hello"})
        assert response.status_code == 200 and "[DONE]" in response.text
        task_id = response.headers["X-EVA-Task-ID"]
        view = container.runtime.results.lookup(task_id)
        event_id = view["event_id"]
    else:
        queued = client.post("/api/chat", json={"text": "Hello"}).json()
        task_id, event_id = queued["task_id"], queued["event_id"]
        assert container.runtime.results.wait(task_id, timeout=10) is not None
    assert calls == [True] and effect.read_text() == "private-tool-content"
    receipts = journal.tool_receipts(event_id)
    assert [r.tool_id for r in receipts] == [
        "tool:receipt_probe",
        "executor:file:write",
    ]
    assert all(r.status == "completed" and r.event_id == event_id for r in receipts)
    assert receipts[1].parent_action_id == receipts[0].action_id
    assert all("private-tool-content" not in r.model_dump_json() for r in receipts)
    episode = journal.get_by_event(event_id)
    assert episode is not None and len(episode.actions) == 3
    assert receipts[0].parent_action_id == episode.actions[0].action_id
    registered = client.post("/api/goals", json=goal_body(task_id)).json()
    reference = client.get(f"/api/goals/{registered['goal_id']}/episode").json()
    assert reference["episode"]["tool_receipts"]["status"] == "available"
    assert len(reference["episode"]["tool_receipts"]["receipts"]) == 2
    assert registered["status"] == "pending_verification"
