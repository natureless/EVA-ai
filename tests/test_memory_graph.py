"""Graph truthfulness, legacy reads, projection bounds and vault ownership."""
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.memory_preview import create_app
from connectors.obsidian import INDEX, render_notes, sync_vault, zip_notes
from memory.graph_projection import dangling_world_relations, memory_node, note_path, project_memory, read_database
from memory.graph_neighbors import memory_neighbors


@pytest.fixture
def graph_db(tmp_path):
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parents[1] / "migrations/001_initial_schema.sql").read_text())
        connection.execute("ALTER TABLE working_memory ADD COLUMN provenance_json TEXT DEFAULT '{}'")
        connection.execute("ALTER TABLE world_entities ADD COLUMN provenance_json TEXT DEFAULT '{}'")
        for eid in ["event-old", *[f"e{i:03}" for i in range(80)]]:
            connection.execute("INSERT INTO events VALUES (?, 'user_message', 'chat', '2026-01-01', ?, NULL, 'completed')", (eid, json.dumps({"text": "测试 " + eid})))
        for i in range(20):
            origin = {"schema_version": 1, "epistemic_status": "assistant_inference", "source": "chat", "source_event_id": "event-old"}
            connection.execute("INSERT INTO working_memory VALUES (?, ?, '', 'chat', 2, '[]', '2026-01-01', '2999-01-01', ?)", (f"wm{i}", "记忆 " + str(i), json.dumps(origin)))
        connection.execute("INSERT INTO working_memory VALUES ('expired','过期秘密','','chat',2,'[]','2000-01-01','2001-01-01','{}')")
        connection.execute("INSERT INTO long_term_memory VALUES ('ltm','长期保存','general','',0.8,'event-old','active','2026-01-01')")
        connection.execute("INSERT INTO long_term_memory VALUES ('deleted','已删除','general','',0.8,'','deleted','2026-01-01')")
        for i in range(5):
            connection.execute("INSERT INTO world_entities VALUES (?, 'task', ?, '{}', '2026-01-01', '{}')", (f"w{i}", f"世界实体{i}"))
        for source, target, relation in [('w0','w1','owns'),('w0','w1','reviews'),('w1','w2','uses'),('w0','missing','dangling')]:
            connection.execute("INSERT INTO world_edges VALUES (?, ?, ?, .9, '2026-01-01')", (source,target,relation))
    return path


def test_graph_is_bounded_and_relations_are_explicit(graph_db):
    graph = project_memory(graph_db, limit=50)
    ids = {node["id"] for node in graph["nodes"]}
    assert len(ids) == 50
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
    world = [edge for edge in graph["edges"] if edge["kind"] == "stored_relation"]
    assert {edge["relation"] for edge in world} == {"owns", "reviews", "uses"}
    assert len({edge["id"] for edge in world}) == 3
    assert graph["scope"]["dangling_world_edges"] == 1
    assert graph["scope"]["nodes_truncated"]
    assert graph["counts"]["S1"]["available"] is False
    assert graph["counts"]["S2"]["total"] == 20
    assert graph["counts"]["S3"]["total"] == 1
    assert "S5:event-old" in ids  # outside the recent archive seed
    assert any(e["kind"] == "provenance" and e["target"] == "S5:event-old" for e in graph["edges"])
    assert all(e["provenance"]["epistemic_status"] == "unknown" for e in world)


def test_reads_preserve_database_and_legacy_schema(graph_db):
    before = graph_db.read_bytes()
    project_memory(graph_db)
    assert memory_node(graph_db, "S4", "w0")["provenance"]["epistemic_status"] == "unknown"
    assert memory_node(graph_db, "S2", "expired") is None
    assert memory_node(graph_db, "S3", "deleted") is None
    with read_database(graph_db) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM events")
        assert "event_contract" not in [row[1] for row in connection.execute("PRAGMA table_info(events)")]
    assert graph_db.read_bytes() == before


def test_diagnostic_record_lookup_reaches_beyond_projection_without_writes(graph_db):
    before = graph_db.read_bytes()
    with TestClient(create_app(graph_db, graph_db.parent)) as client:
        projection = client.get('/api/memory/graph', params={'limit': 1}).json()
        assert 'S5:event-old' not in {node['id'] for node in projection['nodes']}
        event = client.get('/api/memory/graph/node', params={'tier': 'S5', 'record_id': 'event-old'})
        assert event.status_code == 200
        assert event.json()['record_id'] == 'event-old'
        assert '测试 event-old' in event.json()['content']
        assert client.get('/api/memory/graph/node', params={
            'tier': 'S5', 'record_id': 'missing-event',
        }).status_code == 404
    assert graph_db.read_bytes() == before


def test_dangling_diagnostics_keep_direction_provenance_and_database_unchanged(graph_db):
    with sqlite3.connect(graph_db) as c:
        c.execute("ALTER TABLE world_edges ADD COLUMN provenance_json TEXT DEFAULT '{}'")
        origin = json.dumps({"epistemic_status": "assistant_inference", "source": "chat_agent",
                             "source_event_id": "event-old"})
        for source, target in [("absent", "w0"), ("absent", "also-absent")]:
            c.execute("INSERT INTO world_edges VALUES (?, ?, 'references', .9, '2026-01-01', ?)",
                      (source, target, origin))
    before = graph_db.read_bytes()
    page = dangling_world_relations(graph_db)
    assert page["total"] == project_memory(graph_db)["scope"]["dangling_world_edges"] == 3
    both, source, target = page["items"]
    assert both["source"]["missing"] and both["target"]["missing"]
    assert source["source"] == {"record_id": "absent", "label": None, "missing": True}
    assert source["target"] == {"record_id": "w0", "label": "世界实体0", "missing": False}
    assert source["provenance"]["epistemic_status"] == "assistant_inference"
    assert source["provenance"]["source_event_id"] == "event-old"
    assert not target["source"]["missing"] and target["target"]["missing"]
    assert target["provenance"]["epistemic_status"] == "unknown"
    assert target["provenance"]["source"] == ""  # endpoint ID is not an attribution
    assert graph_db.read_bytes() == before


def test_dangling_diagnostics_page_distinct_relations_and_ignore_projection_limits(graph_db):
    with sqlite3.connect(graph_db) as c:
        c.execute("INSERT INTO world_edges VALUES ('w0','missing','second-relation',1,'2026-01-01')")
    first = dangling_world_relations(graph_db, limit=1)
    second = dangling_world_relations(graph_db, limit=1, offset=first["next_offset"])
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["relation"] == "dangling"
    assert second["items"][0]["relation"] == "second-relation"
    assert second["next_offset"] is None
    assert dangling_world_relations(graph_db, offset=20)["items"] == []
    assert project_memory(graph_db, limit=1, query="世界实体0")["edges"] == []
    assert all(item["target"]["record_id"] == "missing" for item in dangling_world_relations(graph_db)["items"])
    with sqlite3.connect(graph_db) as c:
        c.execute("DELETE FROM world_edges WHERE target='missing'")
    empty = dangling_world_relations(graph_db)
    assert empty["total"] == 0 and empty["items"] == [] and empty["next_offset"] is None


def test_dangling_api_bounds_legacy_and_missing_schema(graph_db, tmp_path):
    before = graph_db.read_bytes()
    with TestClient(create_app(graph_db, tmp_path)) as client:
        response = client.get("/api/memory/graph/dangling?limit=1")
        assert response.status_code == 200
        assert response.json()["items"][0]["provenance"]["epistemic_status"] == "unknown"
        for params in ("limit=0", "limit=101", "offset=-1", "offset=2147483648"):
            assert client.get("/api/memory/graph/dangling?" + params).status_code == 422
        assert client.post("/api/memory/graph/dangling").status_code == 405
    assert graph_db.read_bytes() == before
    with sqlite3.connect(graph_db) as c:
        c.execute("DROP TABLE world_entities")
    with TestClient(create_app(graph_db, tmp_path)) as client:
        assert client.get("/api/memory/graph/dangling").status_code == 503


def test_dangling_api_uses_runtime_container_and_auth(client):
    assert client.get("/api/memory/graph/dangling").json()["total"] == 0
    client.headers.pop("X-API-Token")
    assert client.get("/api/memory/graph/dangling").status_code == 401


def test_search_and_tiers_do_not_invent_context(graph_db):
    graph = project_memory(graph_db, query="世界实体0", tiers=("S4",))
    assert [n["id"] for n in graph["nodes"]] == ["S4:w0"]
    assert graph["edges"] == []
    assert graph["scope"]["matched_nodes"] == 1
    assert project_memory(graph_db, query="%' OR 1=1 --")["nodes"] == []
    assert project_memory(graph_db, query="过期秘密")["nodes"] == []
    bounded = project_memory(graph_db, tiers=("S4",), edge_limit=1)
    assert len(bounded["edges"]) == 1 and bounded["scope"]["edges_truncated"]


def test_session_is_injected_not_restored_or_mutated(graph_db):
    entries = [("s1", {"content": "刚刚的对话", "provenance": {"epistemic_status": "user_statement"}})]
    graph = project_memory(graph_db, session=entries, tiers=("S1",))
    assert graph["nodes"][0]["id"] == "S1:s1"
    assert graph["counts"]["S1"]["available"]
    graph["nodes"][0]["provenance"]["epistemic_status"] = "unknown"
    assert entries[0][1]["provenance"]["epistemic_status"] == "user_statement"


def test_field_sources_produce_provenance_edges(graph_db):
    provenance = {"schema_version": 1, "epistemic_status": "unknown", "field_provenance": {
        "name": {"schema_version": 1, "epistemic_status": "user_statement", "source_event_id": "event-old"}}}
    with sqlite3.connect(graph_db) as c:
        c.execute("UPDATE world_entities SET provenance_json=? WHERE id='w0'", (json.dumps(provenance),))
    graph = project_memory(graph_db)
    assert any(e["source"] == "S4:w0" and e["target"] == "S5:event-old" and e["kind"] == "provenance" for e in graph["edges"])


def test_export_links_resolve_and_content_stays_literal(graph_db):
    graph = project_memory(graph_db, content_limit=100_000)
    node = graph["nodes"][0]
    node["content"] = '```text\n[[untrusted note]]\n```\n<script>alert(1)</script>'
    files = render_notes(graph)
    note = files[node["note_path"]].decode()
    assert "````text\n```text" in note
    for edge in graph["edges"]:
        target = next(n for n in graph["nodes"] if n["id"] == edge["target"])
        source = next(n for n in graph["nodes"] if n["id"] == edge["source"])
        assert f"[[{target['note_path'][:-3]}|" in files[source["note_path"]].decode()
    with zipfile.ZipFile(io.BytesIO(zip_notes(graph))) as archive:
        assert set(archive.namelist()) == set(files)
        assert all(name.startswith("EVA-Memory/") for name in archive.namelist())
    path = note_path("S4:../../CON", "../../危险#|[]:<script>")
    assert Path(path).parent.as_posix() == "EVA-Memory/notes"
    assert not any(c in Path(path).name for c in '<>:"/\\|?*#[]')


def test_sync_preserves_user_edits_and_stale_notes(graph_db, tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    config = vault / ".obsidian/workspace.json"
    config.write_text('{"user":"layout"}')
    graph = project_memory(graph_db, limit=25)
    first = sync_vault(graph, vault)
    assert first["written"] == 26 and first["conflicts"] == []
    assert sync_vault(graph, vault)["unchanged"] == 26
    name = graph["nodes"][0]["note_path"]
    note = vault / name
    note.write_text("我的整理", encoding="utf-8")
    result = sync_vault(graph, vault)
    assert result["conflicts"] == [name]
    assert note.read_text(encoding="utf-8") == "我的整理"
    smaller = project_memory(graph_db, tiers=("S3",), limit=25)
    result = sync_vault(smaller, vault)
    assert result["retained_outside_projection"] > 0
    assert note.exists() and config.read_text() == '{"user":"layout"}'
    assert "vault=vault&file=EVA-Memory%2FIndex.md" in first["open_uri"]
    assert str(tmp_path) not in first["open_uri"]


def test_sync_refuses_unowned_file_and_nonvault(graph_db, tmp_path):
    graph = project_memory(graph_db, limit=1)
    with pytest.raises(ValueError, match="existing Obsidian vault"):
        sync_vault(graph, tmp_path)
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "EVA-Memory").mkdir()
    (tmp_path / INDEX).write_text("user's index")
    result = sync_vault(graph, tmp_path)
    assert INDEX in result["conflicts"]
    assert (tmp_path / INDEX).read_text() == "user's index"


def test_read_only_preview_apis_and_html(graph_db, tmp_path):
    before = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    with TestClient(create_app(graph_db, tmp_path)) as client:
        page = client.get("/")
        assert page.status_code == 200 and 'id="memoryCanvas"' in page.text
        assert "只读记忆视图" in page.text
        assert client.get("/api/memory/graph?limit=601").status_code == 422
        assert client.get("/api/memory/graph?tiers=INVALID").status_code == 422
        assert client.get("/api/memory/graph?limit=1").json()["scope"]["node_limit"] == 1
        assert client.get("/api/memory/graph/node?tier=S4&record_id=w0").status_code == 200
        assert client.get("/api/memory/graph/node?tier=S4&record_id=missing").status_code == 404
        archive = client.get("/api/obsidian/export?limit=25")
        assert archive.status_code == 200 and zipfile.is_zipfile(io.BytesIO(archive.content))
        assert not (tmp_path / "EVA-Memory").exists()  # GET export only returns bytes
        assert client.post("/api/memory/graph").status_code == 405
    assert hashlib.sha256(graph_db.read_bytes()).hexdigest() == before


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        project_memory(missing)
    assert not missing.exists()


def test_main_runtime_graph_uses_container_database(client):
    result = client.get("/api/memory/graph")
    assert result.status_code == 200
    assert result.json()["scope"]["session_available"] is True


def test_neighbors_reach_outside_search_and_paginate_parallel_relations_read_only(graph_db):
    before = graph_db.read_bytes()
    assert len(project_memory(graph_db, query="世界实体0")["nodes"]) == 1
    first = memory_neighbors(graph_db, "S4", "w0", limit=1)
    second = memory_neighbors(graph_db, "S4", "w0", limit=1, offset=first["next_offset"])
    assert first["has_more"] and second["next_offset"] is None
    assert first["scope"]["missing_endpoints"] == 1
    assert {e["relation"] for page in [first, second] for e in page["edges"]} == {"owns", "reviews"}
    assert {n["id"] for n in first["nodes"]} == {"S4:w0", "S4:w1"}
    assert all("missing" not in n["id"] for page in [first, second] for n in page["nodes"])
    original = {e["id"] for e in project_memory(graph_db)["edges"]}
    assert all(e["id"] in original for page in [first, second] for e in page["edges"])
    assert before == graph_db.read_bytes()


def test_neighbors_support_both_directions_self_edges_and_no_relation_inference(graph_db):
    with sqlite3.connect(graph_db) as c:
        c.execute("INSERT INTO world_edges VALUES ('w1','w1','self',.5,'2026-01-01')")
    page = memory_neighbors(graph_db, "S4", "w1")
    assert {e["relation"] for e in page["edges"]} == {"owns", "reviews", "uses", "self"}
    assert {n["id"] for n in page["nodes"]} == {"S4:w0", "S4:w1", "S4:w2"}
    isolated = memory_neighbors(graph_db, "S4", "w4")
    assert len(isolated["nodes"]) == 1 and isolated["edges"] == []
    assert not isolated["scope"]["incomplete"]


def test_event_neighbors_include_current_record_and_field_sources_not_expired_deleted_or_unrelated_fields(graph_db):
    source = {"schema_version": 1, "source_event_id": "event-old"}
    with sqlite3.connect(graph_db) as c:
        c.execute("UPDATE world_entities SET provenance_json=? WHERE id='w0'", (json.dumps({
            "schema_version": 1, "field_provenance": {"name": source, "properties.removed": source}}),))
        c.execute("UPDATE world_entities SET provenance_json=? WHERE id='w1'", (json.dumps({
            "schema_version": 1, "field_provenance": {"properties.removed": source}}),))
        c.execute("UPDATE working_memory SET provenance_json=? WHERE id='expired'", (json.dumps(source),))
        c.execute("UPDATE long_term_memory SET source_event_id='event-old' WHERE id='deleted'")
    session = [("session", {"content": "session source", "provenance": source})]
    page = memory_neighbors(graph_db, "S5", "event-old", session=session)
    ids = {n["id"] for n in page["nodes"]}
    assert {"S4:w0", "S3:ltm", "S1:session", "S2:wm0"} <= ids
    assert not {"S4:w1", "S2:expired", "S3:deleted"} & ids
    assert len(page["edges"]) == 23
    assert all(e["target"] == "S5:event-old" for e in page["edges"])
    forward = memory_neighbors(graph_db, "S4", "w0")
    assert any(e["target"] == "S5:event-old" for e in forward["edges"])


def test_event_neighbor_json_matching_handles_escaped_ids_and_corrupt_history(graph_db):
    ref = '事件"\\甲'
    with sqlite3.connect(graph_db) as c:
        c.execute("INSERT INTO events VALUES (?, 'user_message','chat','2026-01-01','{}',NULL,'completed')", (ref,))
        c.execute("UPDATE world_entities SET provenance_json=? WHERE id='w0'", (json.dumps({"source_event_id": ref}),))
        c.execute("UPDATE world_entities SET provenance_json='{broken' WHERE id='w1'")
    result = memory_neighbors(graph_db, "S5", ref)
    assert [e["source"] for e in result["edges"]] == ["S4:w0"]
    assert result["scope"]["incomplete"] is False


def test_neighbor_missing_sources_report_incomplete_and_missing_center_is_not_empty_success(graph_db):
    assert memory_neighbors(graph_db, "S4", "missing") is None
    assert memory_neighbors(graph_db, "S2", "expired") is None
    with sqlite3.connect(graph_db) as c:
        c.execute("DROP TABLE world_edges")
    page = memory_neighbors(graph_db, "S4", "w0")
    assert page["scope"]["incomplete"] and page["warnings"]


def test_neighbor_scan_limit_does_not_claim_exhaustive_empty_result(graph_db):
    # References on removed fields are candidates, but not edges in the graph contract.
    raw = json.dumps({"schema_version": 1, "field_provenance": {
        "properties.removed": {"source_event_id": "event-old"}}})
    with sqlite3.connect(graph_db) as c:
        c.executemany("INSERT INTO world_entities VALUES (?, 'task', 'candidate', '{}', '2026-01-01', ?)",
                      [(f"candidate-{i:05}", raw) for i in range(5001)])
    result = memory_neighbors(graph_db, "S5", "event-old", limit=100)
    assert result["scope"]["scan_truncated"] and result["scope"]["incomplete"]


def test_neighbor_api_is_bounded_and_uses_injected_database(graph_db, tmp_path, client):
    with TestClient(create_app(graph_db, tmp_path)) as preview:
        for query in ["tier=BAD&record_id=w0", "tier=S4&record_id=w0&limit=101", "tier=S4&record_id=w0&offset=3001"]:
            assert preview.get('/api/memory/graph/neighbors?' + query).status_code == 422
        assert preview.get('/api/memory/graph/neighbors?tier=S4&record_id=missing').status_code == 404
        response = preview.get('/api/memory/graph/neighbors?tier=S4&record_id=w0')
        assert response.status_code == 200 and len(response.json()["edges"]) == 2
        assert "obsidian_uri" in response.json()["nodes"][0]
    # The full service fixture's database does not contain preview w0.
    assert client.get('/api/memory/graph/neighbors?tier=S4&record_id=w0').status_code == 404
    assert client.get('/api/memory/graph/neighbors?tier=S4&record_id=w0',
                      headers={"X-API-Token": ""}).status_code == 401
