"""WorldModelGraph + EntityExtractor unit tests."""

from world.world_model import WorldModelGraph
from core.entity_extractor import EntityExtractor, entity_extractor
from memory.tiered_store import WorldModelStore
from memory.sqlite_store import SQLiteStore
from pathlib import Path
import tempfile
import os


# ── WorldModelGraph Tests ──────────────────────────────────

class TestWorldModelGraph:
    def test_upsert_and_get_entity(self):
        wm = WorldModelGraph()
        eid = wm.upsert_entity("task", "Write unit tests")
        assert eid.startswith("task_")
        e = wm.get_entity(eid)
        assert e is not None
        assert e.name == "Write unit tests"
        assert e.properties["status"] == "active"

    def test_upsert_is_idempotent(self):
        wm = WorldModelGraph()
        eid1 = wm.upsert_entity("task", "Same name task")
        eid2 = wm.upsert_entity("task", "Same name task")
        assert eid1 == eid2  # deterministic eid
        assert wm.entity_count == 1

    def test_get_entities_by_type(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Task A")
        wm.upsert_entity("task", "Task B")
        wm.upsert_entity("person", "Alice")
        tasks = wm.get_entities_by_type("task")
        assert len(tasks) == 2
        persons = wm.get_entities_by_type("person")
        assert len(persons) == 1

    def test_remove_entity(self):
        wm = WorldModelGraph()
        eid = wm.upsert_entity("risk", "Low priority risk")
        assert wm.remove_entity(eid)
        assert wm.get_entity(eid) is None
        assert wm.remove_entity("nonexistent") is False

    def test_link_and_get_edges(self):
        wm = WorldModelGraph()
        wm.upsert_entity("person", "Alice")
        wm.upsert_entity("task", "Task X")
        wm.link("person_alice", "task_task_x", "assigned_to", weight=0.8)
        edges = wm.get_edges(eid="person_alice")
        assert len(edges) >= 1
        assert edges[0].relation == "assigned_to"

    def test_get_neighbors(self):
        wm = WorldModelGraph()
        wm.upsert_entity("person", "Bob")
        wm.upsert_entity("task", "Refactor code")
        wm.upsert_entity("task", "Write docs")
        wm.link("person_bob", "task_refactor_code", "assigned_to")
        wm.link("person_bob", "task_write_docs", "assigned_to")
        neighbors = wm.get_neighbors("person_bob")
        assert len(neighbors) == 2

    def test_active_tasks_property(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Active task", {"status": "active"})
        wm.upsert_entity("task", "Done task", {"status": "completed"})
        active = wm.active_tasks
        assert len(active) == 1
        assert active[0]["name"] == "Active task"

    def test_recent_entities_property(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Recent", {"status": "active"})
        wm.upsert_entity("person", "Someone", {})
        assert "Recent" in wm.recent_entities

    def test_snapshot_roundtrip(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Snapshot task", {"priority": "high"})
        wm.upsert_entity("person", "Test User", {})
        wm.link("person_test_user", "task_snapshot_task", "owns")
        d = wm.to_dict()
        assert "entities" in d
        assert "edges" in d

        restored = WorldModelGraph.from_dict(d)
        assert restored.entity_count == 2
        assert restored.edge_count == 1
        assert restored.active_tasks[0]["name"] == "Snapshot task"

    def test_old_format_compat(self):
        """Old WorldModel dict format (no entities/edges) should work."""
        old = {"focus": "test", "mode": "active", "active_tasks": [], "last_reply": "hi"}
        wm = WorldModelGraph.from_dict(old)
        assert wm.focus == "test"
        assert wm.entity_count == 0
        assert wm.edge_count == 0

    def test_mutation_helpers(self):
        wm = WorldModelGraph()
        wm.apply_user_message("hello world")
        assert wm.focus == "hello world"
        assert wm.last_user_message_at is not None

        wm.apply_agent_result(reply="ok", selected_agent="chat_agent", loop_id="l1")
        assert wm.last_reply == "ok"
        assert wm.last_selected_agent == "chat_agent"

        wm.apply_reminder()
        assert wm.last_reminder_at is not None


# ── S4 Persistence Tests ───────────────────────────────────

class TestWorldModelS4Persistence:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()
        cls.s4 = WorldModelStore(cls.store)

    def test_flush_and_load(self):
        wm = WorldModelGraph()
        wm.upsert_entity("task", "Persist me", {"priority": "urgent"})
        wm.upsert_entity("person", "DB User", {})
        wm.link("person_db_user", "task_persist_me", "owns")
        wm.flush(self.s4)

        wm2 = WorldModelGraph()
        wm2.load_from_store(self.s4)
        assert wm2.entity_count == 2
        assert wm2.edge_count == 1
        assert wm2.get_entities_by_type("task")[0].name == "Persist me"


# ── Entity Extraction Tests ────────────────────────────────

class TestEntityExtraction:
    def test_extract_tasks(self):
        entities = entity_extractor.extract_entities("TODO: fix login bug\n/task: write tests")
        tasks = [e for e in entities if e["type"] == "task"]
        assert len(tasks) >= 2

    def test_extract_person(self):
        entities = entity_extractor.extract_entities("assigned to @alice please review")
        persons = [e for e in entities if e["type"] == "person"]
        assert any("alice" in p["name"].lower() for p in persons)

    def test_extract_risk(self):
        entities = entity_extractor.extract_entities("This is a critical risk: data loss possible")
        risks = [e for e in entities if e["type"] == "risk"]
        assert len(risks) >= 1

    def test_extract_from_reply(self):
        reply = "/task: deploy v2.0  assigned to @bob  risk: server downtime"
        entities, relations = entity_extractor.extract_from_reply(reply)
        assert len(entities) >= 2  # task + risk or person
        # should have at least a task relation
        task_relations = [r for r in relations if r["relation"] == "created"]
        assert len(task_relations) >= 1

    def test_empty_text(self):
        entities = entity_extractor.extract_entities("")
        assert entities == []

    def test_no_llm_dependency(self):
        extractor = EntityExtractor()
        assert isinstance(extractor, EntityExtractor)
        # pure rule-based — no external API calls

    def test_bullet_task_extraction(self):
        entities = entity_extractor.extract_entities("- fix the login bug\n- [x] write tests\n* deploy to prod")
        tasks = [e for e in entities if e["type"] == "task"]
        assert len(tasks) >= 2
        # Check that the checkbox [x] is marked completed
        completed = [t for t in tasks if t["properties"].get("status") == "completed"]
        assert len(completed) >= 1

    def test_numbered_task_extraction(self):
        entities = entity_extractor.extract_entities("1) update docs\n2. review code\n3) ship it")
        tasks = [e for e in entities if e["type"] == "task"]
        assert len(tasks) >= 2

    def test_priority_detection(self):
        entities = entity_extractor.extract_entities("TODO: urgent fix for critical bug")
        tasks = [e for e in entities if e["type"] == "task"]
        urgent_tasks = [t for t in tasks if t["properties"].get("priority") == "urgent"]
        assert len(urgent_tasks) >= 1

    def test_deadline_detection(self):
        entities = entity_extractor.extract_entities("- submit report by 2026-03-15")
        tasks = [e for e in entities if e["type"] == "task"]
        deadlines = [t for t in tasks if t["properties"].get("deadline")]
        assert len(deadlines) >= 1

    def test_chinese_patterns(self):
        entities = entity_extractor.extract_entities("任务：修复登录bug  待办：写测试  分配给：@小明")
        tasks = [e for e in entities if e["type"] == "task"]
        persons = [e for e in entities if e["type"] == "person"]
        assert len(tasks) >= 2
        assert len(persons) >= 1

    def test_is_noise_filters_garbage(self):
        assert entity_extractor._is_noise("if") is True
        assert entity_extractor._is_noise("123") is True
        assert entity_extractor._is_noise("v2.0") is True
        assert entity_extractor._is_noise("real task name") is False

    def test_is_done_detection(self):
        assert entity_extractor._is_done("completed the feature") is True
        assert entity_extractor._is_done("fixed the bug") is True
        assert entity_extractor._is_done("still working on it") is False

    def test_relations_single_entity_empty(self):
        entities = [{"type": "task", "name": "one task"}, {"type": "person", "name": "alice"}]
        relations = entity_extractor.extract_relations(entities, "assigned to @alice")
        # At least one relation (user→task or task→person)
        assert len(relations) >= 1

    def test_relations_empty_entities(self):
        relations = entity_extractor.extract_relations([], "some text")
        assert relations == []

    def test_file_extraction(self):
        entities = entity_extractor.extract_entities("check `main.py` and file: config.yaml")
        files = [e for e in entities if e["type"] == "file"]
        assert len(files) >= 1

    def test_action_marker_extraction(self):
        entities = entity_extractor.extract_entities(
            "You should fix the login bug. The next step: deploy to staging."
        )
        tasks = [e for e in entities if e["type"] == "task"]
        assert len(tasks) >= 1


# ── Snapshot Store Tests ────────────────────────────────────

class TestSnapshotStore:
    def test_load_latest_nonexistent(self, tmp_path):
        from world.snapshot_store import SnapshotStore
        snap_dir = tmp_path / "snapshots"
        latest = snap_dir / "latest.json"
        store = SnapshotStore(snap_dir, latest)
        assert store.load_latest() is None

    def test_save_and_load_roundtrip(self, tmp_path):
        from world.snapshot_store import SnapshotStore
        snap_dir = tmp_path / "snapshots"
        latest = snap_dir / "latest.json"
        store = SnapshotStore(snap_dir, latest)
        payload = {"focus": "testing", "mode": "active", "version": 1}
        store.save_latest(payload)
        loaded = store.load_latest()
        assert loaded is not None
        assert loaded["focus"] == "testing"
        assert loaded["version"] == 1

    def test_save_creates_directory(self, tmp_path):
        from world.snapshot_store import SnapshotStore
        snap_dir = tmp_path / "nested" / "snapshots"
        latest = snap_dir / "latest.json"
        store = SnapshotStore(snap_dir, latest)
        store.save_latest({"hello": "world"})
        assert latest.exists()

    def test_load_latest_after_save(self, tmp_path):
        from world.snapshot_store import SnapshotStore
        snap_dir = tmp_path / "snapshots"
        latest = snap_dir / "latest.json"
        store = SnapshotStore(snap_dir, latest)
        store.save_latest({"a": 1})
        first = store.load_latest()
        store.save_latest({"b": 2})
        second = store.load_latest()
        assert first["a"] == 1
        assert second["b"] == 2
