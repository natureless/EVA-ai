"""Context reads bound payload allocation and never walk graph edges."""

import pytest

from world.world_model import WorldModelGraph


class NoEdgeTraversal(dict):
    def values(self):
        raise AssertionError("context must not traverse edges")

    def __iter__(self):
        raise AssertionError("context must not traverse edges")


def test_context_ignores_edges_and_unbounded_properties():
    model = WorldModelGraph(focus="f" * 1000)
    for i in range(50):
        model.upsert_entity("task", f"Task {i}", {"payload": {"large": "x" * 100000}, "priority": "high"})
    model._edges = NoEdgeTraversal({("a", "b", "owns"): object()})
    projection = model.context_projection(task_limit=3, entity_limit=2)
    assert len(projection["active_tasks"]) == 3
    assert len(projection["recent_entities"]) == 2
    assert len(projection["focus"]) == 160
    assert projection["counts"] == {"entities": 50, "edges": 1}
    assert set(projection["active_tasks"][0]) == {"id", "name", "status", "priority", "provenance", "field_provenance"}
    projection["active_tasks"][0]["status"] = "changed"
    assert model.get_entities_by_type("task")[0].properties["status"] == "active"


def test_recent_selection_and_zero_limits():
    model = WorldModelGraph.from_dict({"entities": [
        {"id": "old", "type": "task", "name": "Old", "properties": {"status": "completed"}, "updated_at": "2020"},
        {"id": "new", "type": "person", "name": "New", "updated_at": "2026"},
    ]})
    assert model.context_projection(entity_limit=1)["recent_entities"] == ["New"]
    assert model.context_projection()["active_tasks"] == []
    result = model.context_projection(task_limit=0, entity_limit=0)
    assert result["active_tasks"] == result["recent_entities"] == []


@pytest.mark.parametrize("limit", [-1, 101, True, 1.5])
def test_projection_rejects_invalid_limits(limit):
    with pytest.raises(ValueError):
        WorldModelGraph().context_projection(task_limit=limit)


def test_minimal_probe_uses_projection_not_snapshot(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from packages.minimal_brain import kernel
    from packages.minimal_brain.integration import create_minimal_brain

    model = WorldModelGraph()
    model.upsert_entity("task", "Keep context small")
    monkeypatch.setattr(model, "to_dict", MagicMock(side_effect=AssertionError("full snapshot read")))
    captured = {}
    monkeypatch.setattr(kernel, "MinimalBrainKernel", lambda **kwargs: captured.update(kwargs))
    container = SimpleNamespace(
        snapshot_store=SimpleNamespace(load_latest=lambda: {}),
        runtime=SimpleNamespace(processor=MagicMock()),
        event_bus=MagicMock(), world_model=model, self_model={}, system_state={},
        tiered_memory=SimpleNamespace(s1=SimpleNamespace(list_all=lambda: [])),
    )
    create_minimal_brain(container, SimpleNamespace(
        enable_mvsc_pipeline=False, minimal_brain_queue_capacity=8, minimal_brain_poll_sec=0.02,
    ))
    view = captured["model_probe"]()
    assert view["world"]["counts"]["entities"] == 1
    assert view["world"]["active_tasks"][0]["name"] == "Keep context small"
    model.to_dict.assert_not_called()
