"""Historical references remain separate from present-day memory records."""

from copy import deepcopy
import json

import pytest

from memory.action_context_graph import project_action_context


@pytest.fixture
def view():
    def reference(kind, scope):
        return {"kind": kind, "scope": scope, "revision": 7, "integrity_hash": "a" * 64}

    return {
        "task_id": "task",
        "event_id": "event",
        "parent_action_id": "parent",
        "selected_agent": "chat_agent",
        "intent": {"action_id": "action", "state_version": 2, "request_hash": "b" * 64},
        "inputs": {
            "world": reference("world_context_projection", "world-instance"),
            "memory": reference("sqlite_s2_s3_recall", "durable-memory"),
            "memory_items": [
                {"tier": "S2", "memory_id": f"memory-{i}", "integrity_hash": "c" * 64}
                for i in range(10)
            ],
            "context_hash": "d" * 64,
            "task_hash": "e" * 64,
        },
        "observation": {
            "action_id": "action",
            "source_event_id": "event",
            "state_version": 2,
            "request_hash": "b" * 64,
            "status": "unknown",
            "started_at": "2026-10-08T00:00:00Z",
            "finished_at": None,
            "sealed_at": "2026-10-08T01:00:00Z",
            "observation_kind": "recovery_unknown",
            "returned_ok": None,
            "claim_token": "secret capability",
            "unrelated": "private payload",
        },
    }


def test_graph_is_bounded_has_no_raw_context_or_claims_and_does_not_mutate(view):
    original = deepcopy(view)
    graph = project_action_context(view)
    assert len(graph["nodes"]) == 13 and len(graph["edges"]) == 12
    assert view == original
    assert graph["nodes"][0]["status"] == "action_unknown"
    assert "secret capability" not in json.dumps(graph)
    assert "private payload" not in json.dumps(graph)
    assert "claim_token" not in json.dumps(graph)
    identifiers = {node["id"] for node in graph["nodes"]}
    assert len(identifiers) == 13
    assert all(
        e["source"] in identifiers and e["target"] in identifiers
        for e in graph["edges"]
    )
    assert all(node["tier"] == "I" for node in graph["nodes"][1:])


def test_historical_identity_includes_scope_revision_and_content_hash(view):
    before = project_action_context(view)
    view["inputs"]["memory"]["scope"] = "different-database"
    after = project_action_context(view)
    assert before["nodes"][1]["id"] == after["nodes"][1]["id"]
    assert before["nodes"][2]["id"] != after["nodes"][2]["id"]
    assert before["nodes"][3]["id"] != after["nodes"][3]["id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_id", "wrong"),
        ("source_event_id", "wrong"),
        ("state_version", 99),
        ("request_hash", "f" * 64),
    ],
)
def test_mismatched_observation_is_not_linked(view, field, value):
    view["observation"][field] = value
    with pytest.raises(ValueError):
        project_action_context(view)


def test_missing_input_and_oversized_or_duplicate_input_set_rejected(view):
    view["inputs"]["memory_items"].append(view["inputs"]["memory_items"][0])
    with pytest.raises(ValueError):
        project_action_context(view)
    view["inputs"]["memory_items"] = view["inputs"]["memory_items"][:10]
    view["inputs"]["world"] = None
    with pytest.raises(ValueError):
        project_action_context(view)
