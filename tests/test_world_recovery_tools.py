"""Backup, migration-copy and rollback drills with isolated SQLite/WAL data."""

import hashlib
import json
import sqlite3

import pytest

from memory.sqlite_store import SQLiteStore
from memory.provenance import EpistemicStatus, provenance
from memory.tiered_store import WorldModelStore
from world.recovery_tools import prepare_recovery, restore_recovery, verify_recovery


@pytest.fixture
def sources(tmp_path):
    database = tmp_path / "source.db"
    storage = SQLiteStore(database)
    storage.init_db()
    s4 = WorldModelStore(storage)
    s4.upsert_entity("task_a", "task", "DB task", {"status": "active"})
    s4.upsert_edge("user", "task_a", "owns", 0.9)
    storage.execute("UPDATE world_edges SET updated_at = '2026-09-12T00:00:00Z'")
    original = {
        "self_model": {"name": "keep identity"},
        "minimal_brain": {"goals": [{"event_id": "retain-goal"}]},
        "extension": {"preserve": [1, 2, 3]},
        "world_model": {
            "focus": "do not reset", "updated_at": "2026-09-01T00:00:00Z",
            "entities": [{"id": "file_unique", "type": "file", "name": "Snapshot only", "properties": {}}],
            "edges": [
                {"source": "user", "target": "task_a", "relation": "owns", "weight": 0.2},
                {"source": "user", "target": "task_a", "relation": "owns", "weight": 0.5},
                {"source": "user", "target": "file_unique", "relation": "references", "weight": 1.0},
            ],
        },
    }
    snapshot = tmp_path / "source.json"
    snapshot.write_text(json.dumps(original), encoding="utf-8")
    # Keep the connection open to exercise committed data still present in WAL.
    try:
        yield snapshot, database, original
    finally:
        storage.close()


def test_prepare_captures_wal_and_reports_conflicts_without_changing_sources(sources, tmp_path):
    snapshot, database, original = sources
    before = snapshot.read_bytes()
    bundle = tmp_path / "bundle"
    report = prepare_recovery(snapshot, database, bundle)
    assert snapshot.read_bytes() == before
    assert (bundle / "original.snapshot.json").read_bytes() == before
    assert report["edges"]["duplicate_records"] == 1
    assert report["edges"]["conflicting_snapshot_keys"] == 1
    assert report["edges"]["snapshot_s4_payload_conflict_keys"] == 1
    assert report["edges"]["candidate_records"] == 2
    assert report["entities"]["candidate_records"] == 2
    candidate = json.loads((bundle / "candidate.snapshot.json").read_bytes())
    for key in ("self_model", "minimal_brain", "extension"):
        assert candidate[key] == original[key]
    assert candidate["world_model"]["edges"][0]["weight"] == 0.9
    assert verify_recovery(bundle) == report
    # Hash-only diagnostics do not print private names/contents.
    assert "Snapshot only" not in (bundle / "report.json").read_text(encoding="utf-8")
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM world_entities").fetchone()[0] == 1


@pytest.mark.parametrize("variant", ["original", "candidate"])
def test_restore_copies_verified_bundle_and_supports_exact_rollback(sources, tmp_path, variant):
    snapshot, database, _ = sources
    bundle, restored = tmp_path / "bundle", tmp_path / "restored"
    prepare_recovery(snapshot, database, bundle)
    receipt = restore_recovery(bundle, restored, variant=variant)
    assert receipt["verified"] is True
    assert (restored / "latest.json").read_bytes() == (bundle / f"{variant}.snapshot.json").read_bytes()
    assert (restored / "eva.db").read_bytes() == (bundle / "database.sqlite").read_bytes()
    with sqlite3.connect(restored / "eva.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT weight FROM world_edges").fetchone()[0] == 0.9


def test_second_prepare_on_candidate_is_idempotent(sources, tmp_path):
    snapshot, database, _ = sources
    first, second = tmp_path / "first", tmp_path / "second"
    prepare_recovery(snapshot, database, first)
    prepare_recovery(first / "candidate.snapshot.json", first / "database.sqlite", second)
    assert (first / "candidate.snapshot.json").read_bytes() == (second / "candidate.snapshot.json").read_bytes()


@pytest.mark.parametrize("filename", ["original.snapshot.json", "database.sqlite", "candidate.snapshot.json", "report.json"])
def test_tampered_bundle_is_refused_before_restore(sources, tmp_path, filename):
    snapshot, database, _ = sources
    bundle = tmp_path / "bundle"
    prepare_recovery(snapshot, database, bundle)
    with (bundle / filename).open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore_recovery(bundle, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_candidate_must_match_reconstruction_even_if_hash_is_updated(sources, tmp_path):
    snapshot, database, _ = sources
    bundle = tmp_path / "bundle"
    prepare_recovery(snapshot, database, bundle)
    path = bundle / "candidate.snapshot.json"
    candidate = json.loads(path.read_bytes())
    candidate["extension"] = {"lost": True}
    path.write_text(json.dumps(candidate), encoding="utf-8")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from reconstruction"):
        verify_recovery(bundle)


def test_existing_output_and_source_directory_are_never_overwritten(sources, tmp_path):
    snapshot, database, _ = sources
    raw = snapshot.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_recovery(snapshot, database, snapshot.parent)
    bundle = tmp_path / "bundle"
    prepare_recovery(snapshot, database, bundle)
    with pytest.raises(FileExistsError):
        restore_recovery(bundle, snapshot.parent)
    assert snapshot.read_bytes() == raw


def test_partial_bundle_from_invalid_json_cannot_be_restored(sources, tmp_path):
    snapshot, database, _ = sources
    snapshot.write_bytes(b"{broken")
    bundle = tmp_path / "bundle"
    with pytest.raises(ValueError):
        prepare_recovery(snapshot, database, bundle)
    assert (bundle / "original.snapshot.json").read_bytes() == b"{broken"
    assert not (bundle / "manifest.json").exists()
    with pytest.raises(FileNotFoundError):
        verify_recovery(bundle)


def test_changed_source_snapshot_leaves_incomplete_bundle(sources, tmp_path, monkeypatch):
    from world import recovery_tools
    snapshot, database, _ = sources
    backup = recovery_tools._sqlite_backup

    def concurrent_writer(source, target):
        backup(source, target)
        snapshot.write_bytes(b"{}")

    monkeypatch.setattr(recovery_tools, "_sqlite_backup", concurrent_writer)
    with pytest.raises(RuntimeError, match="changed during backup"):
        prepare_recovery(snapshot, database, tmp_path / "bundle")
    assert not (tmp_path / "bundle" / "manifest.json").exists()


def test_metadata_only_conflicts_are_visible_and_restore_winner_origin(sources, tmp_path):
    snapshot, database, original = sources
    stored = SQLiteStore(database)
    try:
        inferred = provenance(EpistemicStatus.ASSISTANT_INFERENCE, source="assistant", source_event_id="e1")
        observed = provenance(EpistemicStatus.TOOL_OBSERVATION, source="tool", source_event_id="e2")
        s4 = WorldModelStore(stored)
        s4.upsert_entity("p", "project", "Same payload", {}, origin=observed)
        s4.upsert_edge("user", "p", "owns", 1.0, origin=observed)
        original["world_model"] = {
            "entities": [{"id": "p", "type": "project", "name": "Same payload", "properties": {}, "provenance": inferred}],
            "edges": [{"source": "user", "target": "p", "relation": "owns", "weight": 1.0, "provenance": inferred}],
        }
        snapshot.write_text(json.dumps(original), encoding="utf-8")
        bundle = tmp_path / "bundle"
        report = prepare_recovery(snapshot, database, bundle)
        assert report["schema_version"] == 2
        for kind in ("entities", "edges"):
            assert report[kind]["snapshot_s4_payload_conflict_keys"] == 1
            assert report[kind]["conflict_samples"][0]["chosen_matches_s4"] is True
        restore_recovery(bundle, tmp_path / "restored", variant="candidate")
        candidate = json.loads((tmp_path / "restored" / "latest.json").read_bytes())["world_model"]
        assert next(row for row in candidate["entities"] if row["id"] == "p")["provenance"] == observed
        assert next(row for row in candidate["edges"] if row["target"] == "p")["provenance"] == observed
    finally:
        stored.close()


def test_v1_bundle_conflict_fingerprints_remain_verifiable(sources, tmp_path):
    snapshot, database, _ = sources
    bundle = tmp_path / "legacy-bundle"
    prepare_recovery(snapshot, database, bundle)
    # Freeze the previous public representation, including its weight-only hash.
    candidate = json.loads((bundle / "candidate.snapshot.json").read_bytes())
    world = candidate["world_model"]
    world["graph_schema_version"] = 1
    for record in [*world["entities"], *world["edges"], *world["active_tasks"]]:
        record.pop("provenance", None)
        record.pop("field_provenance", None)
    (bundle / "candidate.snapshot.json").write_text(json.dumps(candidate), encoding="utf-8")
    report = json.loads((bundle / "report.json").read_bytes())
    report["schema_version"] = 1
    legacy_weight_hash = hashlib.sha256(b'{\n  "weight": 0.9\n}\n').hexdigest()
    assert report["entities"]["conflict_samples"] == []
    assert len(report["edges"]["conflict_samples"]) == 1
    report["edges"]["conflict_samples"][0]["chosen_payload_sha256"] = legacy_weight_hash
    (bundle / "report.json").write_text(json.dumps(report), encoding="utf-8")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["schema_version"] = 1
    for name in manifest["files"]:
        raw = (bundle / name).read_bytes()
        manifest["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_recovery(bundle) == report
    assert restore_recovery(bundle, tmp_path / "rollback")["verified"] is True
