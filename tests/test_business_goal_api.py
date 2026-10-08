"""Opt-in HTTP registration binds only server receipts and persists across restart."""

import importlib
import hashlib
from threading import Event

import pytest

from memory.episodes import EpisodeRecord, EpisodeResult, StateReference
from packages.kernel.episode_store import EpisodeStore


@pytest.fixture(params=["legacy", "minimal"])
def goal_client(request, monkeypatch, tmp_path):
    monkeypatch.setenv("EVA_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("EVA_ENABLE_BUSINESS_GOALS", "true")
    monkeypatch.setenv(
        "EVA_ENABLE_MINIMAL_BRAIN", str(request.param == "minimal").lower()
    )
    monkeypatch.setenv("EVA_ENABLE_MVSC_PIPELINE", "false")
    return request.getfixturevalue("client")


def request_goal(task_id, **kwargs):
    return {
        "task_id": task_id,
        "description": "verify project readiness",
        "success_conditions": [{"field": "checks.ready", "expected": True}],
        **kwargs,
    }


def test_goal_graph_is_authenticated_readonly_and_does_not_reconcile(
    goal_client, monkeypatch
):
    client = goal_client
    container = client.app.state.container
    store = container.integrations.business_goals
    queued = client.post("/api/chat", json={"text": "hello"}).json()
    assert container.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    goal = store.create(
        task_id=queued["task_id"],
        source_event_id=queued["event_id"],
        description="图谱只读",
        success_conditions=[{"field": "checks.ready", "expected": True}],
    )

    def no_reconcile(*args, **kwargs):
        pytest.fail("graph must not reconcile existing receipts")

    monkeypatch.setattr(store, "record_receipt", no_reconcile)
    before = store._conn.total_changes
    response = client.get("/api/goals/graph?q=图谱")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    graph = response.json()
    assert graph["nodes"][0]["record_id"] == goal["goal_id"]
    assert graph["nodes"][0]["status"] == "active"
    assert graph["edges"] == [] and store._conn.total_changes == before
    for query in ("limit=41", "offset=-1", "q=" + "x" * 201):
        assert client.get("/api/goals/graph?" + query).status_code == 422
    assert (
        client.get("/api/goals/graph", headers={"X-API-Token": "wrong"}).status_code
        == 403
    )
    token = client.headers.pop("X-API-Token")
    try:
        assert client.get("/api/goals/graph").status_code == 401
    finally:
        client.headers["X-API-Token"] = token


def test_goal_graph_reports_disabled_service(client):
    assert client.app.state.container.integrations.business_goals is None
    assert client.get("/api/goals/graph").status_code == 503


def test_real_http_receipt_before_and_after_goal_registration(goal_client, monkeypatch):
    client = goal_client
    container = client.app.state.container
    release = Event()
    original = container.runtime.processor._process_one

    def gated(*args, **kwargs):
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(container.runtime.processor, "_process_one", gated)
    queued = client.post("/api/chat", json={"text": "Hello EVA"}).json()
    try:
        response = client.post("/api/goals", json=request_goal(queued["task_id"]))
        assert response.status_code == 201
        goal = response.json()
        assert goal["status"] == "active"
        assert goal["source_event_id"] == queued["event_id"]
    finally:
        release.set()
    receipt = container.runtime.results.wait(queued["task_id"], timeout=10)
    assert receipt["ok"]
    # No goal GET is needed to persist the execution outcome.
    stored = container.integrations.business_goals.get(goal["goal_id"])
    assert stored["status"] == "pending_verification"
    assert stored["verification_state"] == "not_verified"
    assert client.get(f"/api/goals/{goal['goal_id']}").json() == stored

    queued2 = client.post("/api/chat", json={"text": "Hello again"}).json()
    assert container.runtime.results.wait(queued2["task_id"], timeout=10)["ok"]
    response2 = client.post("/api/goals", json=request_goal(queued2["task_id"]))
    assert response2.status_code == 201
    assert response2.json()["status"] == "pending_verification"
    assert (
        client.get(f"/api/goals/by-task/{queued2['task_id']}").json()["goal_id"]
        == response2.json()["goal_id"]
    )


def test_goal_api_rejects_forged_evidence_and_requires_auth(goal_client):
    client = goal_client
    assert client.post("/api/goals", json=request_goal("unknown")).status_code == 404
    for injected in [
        {"status": "completed"},
        {"observation": {"checks": {"ready": True}}},
        {"source_event_id": "spoof"},
    ]:
        assert (
            client.post(
                "/api/goals", json=request_goal("unknown", **injected)
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/goals",
            json=request_goal(
                "unknown",
                success_conditions=[{"field": "receipt.ok", "expected": True}],
            ),
        ).status_code
        == 422
    )
    assert (
        client.get("/api/goals/unknown", headers={"X-API-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/goals", json=request_goal("unknown"), headers={"X-API-Token": "wrong"}
        ).status_code
        == 403
    )


def test_goals_survive_actual_runtime_restart_without_replaying_work(goal_client):
    client = goal_client
    old = client.app.state.container
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    assert old.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    goal = client.post("/api/goals", json=request_goal(queued["task_id"])).json()
    before_count = old.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
    bootstrap = importlib.import_module("app.bootstrap")
    assert bootstrap.shutdown_system(old)
    assert old.integrations.business_goals._closed
    restarted = bootstrap.bootstrap_system()
    try:
        client.app.state.container = restarted
        restored = client.get(f"/api/goals/{goal['goal_id']}").json()
        assert restored["status"] == "interrupted"
        assert restored["processing_receipt"] == goal["processing_receipt"]
        assert restored["version"] == goal["version"] + 1
        assert (
            restarted.store.fetchone("SELECT COUNT(*) AS n FROM traces")["n"]
            == before_count
        )
        assert restarted.runtime.results.size() == 0
    finally:
        bootstrap.shutdown_system(restarted)
        client.app.state.container = old


def test_cancel_has_version_check_and_does_not_cancel_request(goal_client):
    client = goal_client
    registry = client.app.state.container.runtime.results
    assert registry.create("queued-goal-task", event_id="queued-goal-event")
    goal = client.post("/api/goals", json=request_goal("queued-goal-task")).json()
    endpoint = f"/api/goals/{goal['goal_id']}/cancel"
    assert client.post(endpoint, json={"expected_version": 999}).status_code == 409
    assert (
        client.post(endpoint, json={"expected_version": goal["version"]}).json()[
            "status"
        ]
        == "cancelled"
    )
    assert registry.lookup("queued-goal-task")["state"] == "pending"
    assert (
        client.post("/api/goals", json=request_goal("queued-goal-task")).status_code
        == 409
    )


def test_goal_feature_disabled_by_default(client):
    assert client.post("/api/goals", json=request_goal("unknown")).status_code == 503
    assert client.app.state.container.integrations.business_goals is None


def test_get_reconciles_retained_receipt_after_database_failure(goal_client):
    client = goal_client
    container = client.app.state.container
    store = container.integrations.business_goals
    registry = container.runtime.results
    registry.create("repair-task", event_id="repair-event")
    goal = client.post("/api/goals", json=request_goal("repair-task")).json()
    store._conn.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON business_goal_receipts
                           BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    assert registry.fulfill(
        "repair-task",
        {
            "task_id": "repair-task",
            "event_id": "repair-event",
            "ok": True,
            "terminal_state": "succeeded",
        },
    )
    assert store.get(goal["goal_id"])["status"] == "active"
    assert client.get(f"/api/goals/{goal['goal_id']}").status_code == 503
    assert (
        client.get("/api/runtime").json()["extensions"]["business_goals"][
            "receipt_observer_errors"
        ]
        == 1
    )
    store._conn.execute("DROP TRIGGER fail_receipt")
    assert (
        client.get(f"/api/goals/{goal['goal_id']}").json()["status"]
        == "pending_verification"
    )


def test_registration_race_is_closed_by_second_registry_read(goal_client, monkeypatch):
    client = goal_client
    container = client.app.state.container
    store, registry = container.integrations.business_goals, container.runtime.results
    registry.create("race-task", event_id="race-event")
    original = store.create

    def finish_before_insert(**kwargs):
        registry.fulfill(
            "race-task",
            {
                "task_id": "race-task",
                "event_id": "race-event",
                "ok": True,
                "terminal_state": "succeeded",
            },
        )
        return original(**kwargs)

    monkeypatch.setattr(store, "create", finish_before_insert)
    result = client.post("/api/goals", json=request_goal("race-task"))
    assert result.status_code == 201
    assert result.json()["status"] == "pending_verification"


def file_goal(client, **overrides):
    container = client.app.state.container
    (container.settings.base_dir / "artifact.txt").write_bytes(b"fixed artifact")
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    assert container.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    return client.post(
        "/api/goals",
        json=request_goal(
            queued["task_id"],
            success_conditions=[{"field": "checks.files_match", "expected": True}],
            verification={
                "kind": "workspace_files_sha256_v1",
                "files": [
                    {
                        "path": "artifact.txt",
                        "sha256": hashlib.sha256(b"fixed artifact").hexdigest(),
                    },
                ],
            },
            **overrides,
        ),
    )


def test_http_file_verification_uses_stored_spec_and_server_reads(goal_client):
    client = goal_client
    response = file_goal(client)
    assert response.status_code == 201
    goal = response.json()
    endpoint = f"/api/goals/{goal['goal_id']}/verify"
    assert (
        client.post(
            endpoint,
            json={"expected_version": goal["version"], "checks": {"files_match": True}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            endpoint, json={"expected_version": goal["version"], "verification": {}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            endpoint,
            json={"expected_version": goal["version"]},
            headers={"X-API-Token": "wrong"},
        ).status_code
        == 403
    )
    assert client.post(endpoint, json={"expected_version": 999}).status_code == 409
    completed = client.post(endpoint, json={"expected_version": goal["version"]})
    assert completed.status_code == 200
    result = completed.json()
    assert result["status"] == "completed" and result["verification_state"] == "passed"
    assert result["last_verification"]["verifier_id"] == "workspace_files_sha256_v1"
    assert (
        client.get("/api/runtime").json()["extensions"]["business_goals"][
            "business_verifier_attached"
        ]
        is True
    )
    assert (
        client.post(endpoint, json={"expected_version": result["version"]}).status_code
        == 409
    )


def test_goal_form_contract_recovers_committed_registration_without_auto_verify(
    goal_client, monkeypatch
):
    client = goal_client
    container = client.app.state.container
    routes = importlib.import_module("app.api_routes.routes_goals")
    store = container.integrations.business_goals
    queued = client.post("/api/chat", json={"text": "Hello"}).json()
    assert container.runtime.results.wait(queued["task_id"], timeout=10)["ok"]
    manifest = {
        "kind": "workspace_files_sha256_v1",
        "files": [{"path": "docs/报告 final.md", "sha256": "a" * 64}],
    }
    body = {
        "task_id": queued["task_id"],
        "description": "确认报告内容",
        "deadline": "2030-01-01T04:00:00.000Z",
        "success_conditions": [
            {"field": "checks.files_match", "operator": "equals", "expected": True}
        ],
        "verification": manifest,
    }

    def no_verify(*args, **kwargs):
        pytest.fail("registration and lookup must not inspect files")

    monkeypatch.setattr(store, "verify", no_verify)
    original = routes._reconcile

    def failed_reconcile(*args, **kwargs):
        raise RuntimeError("injected post-commit failure")

    monkeypatch.setattr(routes, "_reconcile", failed_reconcile)
    assert client.post("/api/goals", json=body).status_code == 503
    monkeypatch.setattr(routes, "_reconcile", original)
    recovered = client.get(f"/api/goals/by-task/{queued['task_id']}")
    assert recovered.status_code == 200
    goal = recovered.json()
    assert goal["verification"] == manifest
    assert goal["source_event_id"] == queued["event_id"]
    assert goal["description"] == body["description"]
    assert goal["status"] == "pending_verification"
    assert goal["verification_state"] == "not_verified"
    assert goal["deadline"].startswith("2030-01-01T04:00:00")
    assert (
        client.post(
            "/api/goals", json={**body, "description": "replacement"}
        ).status_code
        == 409
    )
    assert client.get(f"/api/goals/by-task/{queued['task_id']}").json() == goal
    graph = client.get(f"/api/goals/graph?q={goal['goal_id']}").json()
    assert [n["record_id"] for n in graph["nodes"] if n["tier"] == "G"] == [
        goal["goal_id"]
    ]
    assert not any(n["tier"] == "V" for n in graph["nodes"])


def test_goal_episode_endpoint_is_authenticated_read_only_and_uses_real_event(
    goal_client, monkeypatch
):
    client = goal_client
    container = client.app.state.container
    store = container.integrations.business_goals
    registered = file_goal(client).json()
    endpoint = f"/api/goals/{registered['goal_id']}/episode"
    absent = client.get(endpoint)
    assert absent.status_code == 200 and absent.json()["status"] == "not_recorded"
    assert absent.json()["episode"] is None
    episodes = EpisodeStore(container.settings.db_path)
    try:
        recorded = EpisodeRecord(
            event_id=registered["source_event_id"],
            event_type="user_message",
            source="test",
            state_before=StateReference(version=1),
            state_after=StateReference(version=2),
            result=EpisodeResult(
                status="succeeded", observed={"private": "do not project"}
            ),
        )
        episodes.append(recorded)
        before = store._conn.total_changes
        for name in ("get", "record_receipt", "verify", "recover"):
            monkeypatch.setattr(
                store,
                name,
                lambda *args, **kwargs: pytest.fail(
                    "read must not reconcile or verify"
                ),
            )
        response = client.get(endpoint)
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        reference = response.json()
        assert reference["read_only"] and reference["status"] == "available"
        assert reference["episode"]["episode_id"] == recorded.episode_id
        assert reference["episode"]["event_id"] == registered["source_event_id"]
        assert reference["current_status"] == "pending_verification"
        assert reference["current_goal_version"] == registered["version"]
        assert "private" not in response.text
        graph = client.get("/api/goals/graph").json()
        assert graph["counts"]["E"]["total"] == 1
        assert any(e["relation"] == "source_event_episode" for e in graph["edges"])
        assert store._conn.total_changes == before
        assert client.get("/api/goals/missing/episode").status_code == 404
        assert client.get(endpoint, headers={"X-API-Token": "wrong"}).status_code == 403
        token = client.headers.pop("X-API-Token")
        try:
            assert client.get(endpoint).status_code == 401
        finally:
            client.headers["X-API-Token"] = token
    finally:
        episodes.close()


def test_goal_episode_disabled_service_does_not_create_episode_store(client):
    assert client.get("/api/goals/missing/episode").status_code == 503


def test_http_goal_history_is_read_only_and_paginated(goal_client):
    client = goal_client
    goal = file_goal(client).json()
    endpoint = f"/api/goals/{goal['goal_id']}/verify"
    completed = client.post(endpoint, json={"expected_version": goal["version"]}).json()
    history = client.get(f"/api/goals/{goal['goal_id']}/history?limit=1")
    assert history.status_code == 200
    payload = history.json()
    assert payload["read_only"] is True
    assert payload["total"] == 1 and payload["next_offset"] is None
    assert payload["records"][0]["run_id"] == completed["last_verification"]["run_id"]
    assert history.headers["cache-control"] == "no-store"
    assert (
        client.get(f"/api/goals/{goal['goal_id']}/history?limit=51").status_code == 422
    )
    assert client.get("/api/goals/missing/history").status_code == 404
    assert (
        client.get(
            f"/api/goals/{goal['goal_id']}/history",
            headers={"X-API-Token": "wrong"},
        ).status_code
        == 403
    )


def test_http_mismatch_requires_new_server_check(goal_client):
    client = goal_client
    goal = file_goal(client).json()
    target = client.app.state.container.settings.base_dir / "artifact.txt"
    target.write_bytes(b"wrong artifact")
    endpoint = f"/api/goals/{goal['goal_id']}/verify"
    result = client.post(endpoint, json={"expected_version": goal["version"]}).json()
    assert (
        result["status"] == "pending_verification"
        and result["verification_state"] == "mismatch"
    )
    target.write_bytes(b"fixed artifact")
    assert (
        client.post(endpoint, json={"expected_version": result["version"]}).json()[
            "status"
        ]
        == "completed"
    )


def test_legacy_goal_cannot_accept_ad_hoc_verification(goal_client):
    client = goal_client
    registry = client.app.state.container.runtime.results
    registry.create("legacy", event_id="legacy-event")
    registry.fulfill(
        "legacy",
        {
            "task_id": "legacy",
            "event_id": "legacy-event",
            "ok": True,
            "terminal_state": "succeeded",
        },
    )
    goal = client.post("/api/goals", json=request_goal("legacy")).json()
    assert (
        client.post(
            f"/api/goals/{goal['goal_id']}/verify",
            json={"expected_version": goal["version"]},
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/goals/{goal['goal_id']}").json()["status"]
        == "pending_verification"
    )
