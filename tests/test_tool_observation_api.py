"""Authenticated explicit checks, read-only history and both runtime consumers."""

import pytest

from event.event_schema import Event


@pytest.fixture(params=["legacy", "minimal"])
def observation_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_PROCESSING_EPISODES", "true")
    monkeypatch.setenv("EVA_ENABLE_BUSINESS_GOALS", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def setup_unknown(client, tmp_path):
    container = client.app.state.container
    journal = container.integrations.processing_episodes
    journal.begin(
        Event(id="event", type="user_message", source="test", correlation_id="task"),
        "loop",
        {},
    )
    journal.action_started("event", "loop", "chat_agent")
    target = tmp_path / "result.txt"
    action = journal.tool_started(
        "event",
        "loop",
        "executor:file:write",
        {"path": str(target), "content": "real bytes"},
    )
    target.write_text("real bytes", encoding="utf-8")
    journal.record_receipt(
        {
            "event_id": "event",
            "task_id": "task",
            "terminal_state": "outcome_unknown",
            "ok": False,
        }
    )
    store = container.integrations.business_goals
    goal = store.create(
        task_id="task",
        source_event_id="event",
        description="independent",
        success_conditions=[{"field": "checks.files_match", "expected": True}],
    )
    return container, action, goal


def test_explicit_api_samples_fixed_intent_but_never_promotes_goal(
    observation_client, tmp_path, monkeypatch
):
    client = observation_client
    container, action, goal = setup_unknown(client, tmp_path)
    journal = container.integrations.processing_episodes
    endpoint = f"/api/tool-observations/{action}"
    req = {"event_id": "event", "expected_count": 0}
    assert client.post(endpoint, json={**req, "path": "secret"}).status_code == 422
    assert (
        client.post(endpoint, json={**req, "expected_count": True}).status_code == 422
    )
    assert client.post(endpoint, json={**req, "event_id": "other"}).status_code == 409
    assert (
        client.post(endpoint, json=req, headers={"X-API-Token": "bad"}).status_code
        == 403
    )
    assert journal.observation_history("event", action)["count"] == 0
    reply = client.post(endpoint, json=req)
    assert reply.status_code == 201 and reply.headers["cache-control"] == "no-store"
    assert reply.json()["outcome"] == "passed"
    assert client.post(endpoint, json=req).status_code == 409
    assert (
        container.integrations.business_goals.episode_reference(goal["goal_id"])[
            "episode"
        ]["result_status"]
        == "unknown"
    )
    assert container.integrations.business_goals.get(goal["goal_id"]) == goal
    before = journal._conn.total_changes
    monkeypatch.setattr(
        journal.file_verifier, "run", lambda spec: pytest.fail("read must never sample")
    )
    history = client.get(endpoint, params={"event_id": "event"})
    assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
    assert history.json()["records"] == [reply.json()]
    assert (
        client.get(
            endpoint, params={"event_id": "event"}, headers={"X-API-Token": "bad"}
        ).status_code
        == 403
    )
    assert client.get("/api/goals/graph").status_code == 200
    assert journal._conn.total_changes == before


def test_stopped_runtime_cannot_sample(observation_client, tmp_path, monkeypatch):
    client = observation_client
    container, action, _ = setup_unknown(client, tmp_path)
    endpoint = f"/api/tool-observations/{action}"
    req = {"event_id": "event", "expected_count": 0}
    monkeypatch.setattr(
        type(container.runtime.controller), "accepting", property(lambda self: False)
    )
    assert client.post(endpoint, json=req).status_code == 503
    assert client.get(endpoint, params={"event_id": "event"}).json()["count"] == 0


def test_disabled_feature_returns_unavailable(client):
    assert client.get("/api/tool-observations/action?event_id=event").status_code == 503
    assert (
        client.post(
            "/api/tool-observations/action",
            json={"event_id": "event", "expected_count": 0},
        ).status_code
        == 503
    )
