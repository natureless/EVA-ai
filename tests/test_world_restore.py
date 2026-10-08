"""Recovery invariants across the snapshot and the real S4 store."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from memory.sqlite_store import SQLiteStore
from memory.tiered_store import WorldModelStore
from world.world_model import WorldModelGraph


@pytest.fixture
def s4(tmp_path):
    store = SQLiteStore(tmp_path / "world.db")
    store.init_db()
    try:
        yield WorldModelStore(store)
    finally:
        store.close()


def snapshot():
    return {
        "focus": "restore-check",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "entities": [{
            "id": "task_a", "type": "task", "name": "A",
            "properties": {"status": "completed", "nested": {"value": 1}},
            "updated_at": "2026-09-01T00:00:00+00:00",
        }],
        "edges": [{
            "source": "user", "target": "task_a", "relation": "owns",
            "weight": 0.5, "updated_at": "2026-09-01T00:00:00+00:00",
        }],
    }


def test_repeated_snapshot_and_s4_restore_is_identical(s4):
    model = WorldModelGraph.from_dict(snapshot())
    s4.upsert_entity("task_a", "task", "A", {"status": "completed"})
    s4.upsert_edge("user", "task_a", "owns", 0.7)
    model.load_from_store(s4)
    expected = model.to_dict()
    for _ in range(100):
        model = WorldModelGraph.from_dict(model.to_dict())
        model.load_from_store(s4)
        assert model.to_dict() == expected
        assert model.edge_count == 1


def test_duplicate_legacy_snapshot_preserves_distinct_relations():
    data = snapshot()
    data["edges"][0].pop("updated_at")
    data["edges"] *= 100
    data["edges"].append({"source": "user", "target": "task_a", "relation": "checks", "weight": 1})
    model = WorldModelGraph.from_dict(data)
    assert model.edge_count == 2
    assert model.get_entity("task_a").updated_at == data["entities"][0]["updated_at"]
    assert model.get_edges(relation="owns")[0].updated_at == ""
    assert WorldModelGraph.from_dict(model.to_dict()).to_dict() == model.to_dict()


@pytest.mark.parametrize("store_time, expected", [
    ("2026-08-31T23:00:00Z", 0.5),
    ("2026-09-01T08:00:00+08:00", 0.9),  # equal instant: S4 wins
    ("2026-09-02T00:00:00Z", 0.9),
    ("", 0.9),  # unknown time: explicit S4 authority fallback
])
def test_overlap_uses_record_time_then_s4_authority(s4, store_time, expected):
    s4.upsert_entity("task_a", "task", "DB", {"status": "active"})
    s4.upsert_edge("user", "task_a", "owns", 0.9)
    s4.store.execute("UPDATE world_entities SET updated_at = ?", (store_time,))
    s4.store.execute("UPDATE world_edges SET updated_at = ?", (store_time,))
    model = WorldModelGraph.from_dict(snapshot())
    model.load_from_store(s4)
    assert model.get_edges()[0].weight == expected
    entity = model.get_entity("task_a")
    assert entity.name == ("A" if expected == 0.5 else "DB")
    assert ("nested" in entity.properties) == (expected == 0.5)


def test_latest_duplicate_snapshot_record_wins():
    data = snapshot()
    newest = {**data["edges"][0], "weight": 0.8, "updated_at": "2026-09-03T00:00:00Z"}
    data["edges"] = [newest, data["edges"][0], deepcopy(newest)]
    assert WorldModelGraph.from_dict(data).get_edges()[0].weight == 0.8


@pytest.mark.parametrize("version", [3, True, "1", None])
def test_unknown_snapshot_version_is_rejected(version):
    with pytest.raises(ValueError, match="snapshot version"):
        WorldModelGraph.from_dict({**snapshot(), "graph_schema_version": version})


def test_recovery_does_not_write_to_s4(s4):
    s4.upsert_edge("user", "task_a", "owns", 0.7)
    before = s4.list_edges()
    model = WorldModelGraph.from_dict(snapshot())
    model.load_from_store(s4)
    model.flush(s4)
    assert s4.list_edges() == before
    assert s4.get_entity("task_a") is None  # snapshot-only is not silently persisted


def test_keyset_pagination_keeps_all_composite_key_dimensions(s4):
    keys = [("", "", "a"), ("", "", "b"), ("", "a", "a"), ("a", "", "a")]
    for source, target, relation in keys:
        s4.upsert_edge(source, target, relation)
    rows = list(s4.iter_edges(page_size=1))
    assert [(r["source"], r["target"], r["relation"]) for r in rows] == keys


def test_restore_is_complete_beyond_old_5000_limit(s4):
    count = 5101
    s4.store.execute_many(
        "INSERT INTO world_entities (id, type, name, properties_json, updated_at) VALUES (?, 'task', ?, '{}', '')",
        [(f"task_{i:05d}", str(i)) for i in range(count)],
    )
    s4.store.execute_many(
        "INSERT INTO world_edges (source, target, relation, weight, updated_at) VALUES ('user', ?, 'owns', 1, '')",
        [(f"task_{i:05d}",) for i in range(count)],
    )
    model = WorldModelGraph.from_dict(snapshot())
    model.load_from_store(s4)
    assert model.entity_count == count + 1  # snapshot-only entity is retained
    assert model.edge_count == count + 1
    assert model.get_entity("task_05100") is not None
    model.load_from_store(s4)
    assert model.edge_count == count + 1


def test_failed_read_does_not_partially_replace_graph():
    model = WorldModelGraph.from_dict(snapshot())
    expected = model.to_dict()

    def broken_edges():
        raise OSError("read failed")
        yield  # generator failure occurs during iteration

    store = SimpleNamespace(
        iter_entities=lambda: iter([{**snapshot()["entities"][0], "name": "changed"}]),
        iter_edges=broken_edges,
    )
    with pytest.raises(OSError):
        model.load_from_store(store)
    assert model.to_dict() == expected


def test_snapshot_does_not_alias_nested_entity_properties():
    data = snapshot()
    model = WorldModelGraph.from_dict(data)
    data["entities"][0]["properties"]["nested"]["value"] = 2
    exported = model.to_dict()
    exported["entities"][0]["properties"]["nested"]["value"] = 3
    assert model.get_entity("task_a").properties["nested"]["value"] == 1


def test_restore_preserves_unflushed_local_changes(s4):
    model = WorldModelGraph.from_dict(snapshot())
    model.upsert_entity("task", "Local", {"status": "local"}, eid="task_a")
    model.link("user", "task_a", "owns", weight=0.3)
    s4.upsert_entity("task_a", "task", "DB", {})
    s4.upsert_edge("user", "task_a", "owns", 0.9)
    model.load_from_store(s4)
    assert model.get_entity("task_a").name == "Local"
    assert model.get_edges()[0].weight == 0.3
    model.flush(s4)
    assert s4.get_entity("task_a")["name"] == "Local"
    assert s4.list_edges()[0]["weight"] == 0.3


def test_composition_merges_snapshot_and_s4_without_duplicate_edges(s4, tmp_path):
    from app.composition import build_world
    from world.snapshot_store import SnapshotStore

    settings = SimpleNamespace(snapshot_dir=tmp_path, latest_snapshot_path=tmp_path / "latest.json")
    SnapshotStore(tmp_path, settings.latest_snapshot_path).save_latest({"world_model": snapshot()})
    s4.upsert_edge("user", "task_a", "owns", 0.9)
    world = build_world(settings, {}, SimpleNamespace(s4=s4), None)
    assert world.model.edge_count == 1
    assert world.model.focus == "restore-check"
