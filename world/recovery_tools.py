"""Offline recovery bundles: exact backups, conflict reports and verified copies.

No operation overwrites an input path. Publishing a copy into a running EVA
instance is deliberately outside this module's contract.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from memory.tiered_store import WorldModelStore
from memory.provenance import aggregate_provenance, field_provenance, read_provenance
from world.world_model import WorldModelGraph


BUNDLE_FILES = ("original.snapshot.json", "database.sqlite", "candidate.snapshot.json", "report.json")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _new_directory(path: Path) -> Path:
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


@contextmanager
def _backup_store(path: Path):
    # Only completed, closed backup files are immutable; live input DBs are not.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    class ReadOnlyRows:
        def fetchall(self, sql, params=()):
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    try:
        if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("backup database integrity check failed")
        yield WorldModelStore(ReadOnlyRows())
    finally:
        conn.close()


def _sqlite_backup(source: Path, destination: Path) -> None:
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
    # Reserve the path before connecting, so existing output cannot be replaced.
    try:
        _write_new(destination, b"")
        target = sqlite3.connect(str(destination))
        try:
            deadline = time.monotonic() + 30

            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("database backup exceeded 30 seconds; retry with writers stopped")

            conn.backup(target, pages=256, progress=progress, sleep=0.05)
        finally:
            target.close()
    finally:
        conn.close()


def _key(row: dict, kind: str) -> tuple:
    return (row["id"],) if kind == "entities" else (row["source"], row["target"], row["relation"])


def _payload(row: dict, kind: str, *, include_provenance: bool = True) -> dict:
    if kind == "entities":
        value = {"type": row["type"], "name": row["name"], "properties": row.get("properties", {})}
        if include_provenance:
            fields = field_provenance(row, ["name", *[f"properties.{key}" for key in value["properties"]]])
            value.update(provenance=aggregate_provenance(fields), field_provenance=fields)
        return value
    value = {"weight": float(row.get("weight", 1.0))}
    if include_provenance:
        value["provenance"] = read_provenance(row, source_fallback=False)
    return value


def _conflicts(snapshot_rows: list[dict], store_rows: list[dict], merged_rows: list[dict], kind: str, *, include_provenance: bool = True) -> dict:
    def payload_hash(row):
        return _digest(_json_bytes(_payload(row, kind, include_provenance=include_provenance)))
    groups = defaultdict(list)
    for row in snapshot_rows:
        groups[_key(row, kind)].append(row)
    persisted = {_key(row, kind): row for row in store_rows}
    merged = {_key(row, kind): row for row in merged_rows}
    duplicates = conflicts = overlaps = cross_conflicts = timestamp_variants = 0
    details = []
    for key, rows in groups.items():
        variants = {payload_hash(row) for row in rows}
        duplicates += len(rows) - 1
        conflicts += len(variants) > 1
        timestamp_variants += len({row.get("updated_at", "") for row in rows}) > 1
        db_row = persisted.get(key)
        cross = False
        if db_row is not None:
            overlaps += 1
            cross = bool(variants - {payload_hash(db_row)})
            cross_conflicts += cross
        if (len(variants) > 1 or cross) and len(details) < 100:
            winner = payload_hash(merged[key])
            details.append({
                "key_sha256": _digest(_json_bytes(key)),
                "snapshot_records": len(rows), "snapshot_payload_variants": len(variants),
                "s4_overlap": db_row is not None, "chosen_payload_sha256": winner,
                "chosen_matches_s4": db_row is not None and winner == payload_hash(db_row),
            })
    expected_keys = set(groups) | set(persisted)
    if set(merged) != expected_keys:
        raise ValueError("recovery changed distinct graph identities")
    return {
        "snapshot_records": len(snapshot_rows), "snapshot_distinct_keys": len(groups),
        "duplicate_records": duplicates, "conflicting_snapshot_keys": conflicts,
        "snapshot_timestamp_variant_keys": timestamp_variants,
        "s4_records": len(store_rows), "overlapping_keys": overlaps,
        "snapshot_s4_payload_conflict_keys": cross_conflicts,
        "candidate_records": len(merged_rows), "all_distinct_keys_preserved": True,
        "conflict_samples": details, "sample_limit": 100,
    }


def _legacy_graph_view(model: WorldModelGraph) -> dict:
    """Frozen v1 serialization solely for verifying previously issued bundles."""
    graph = model.to_dict()
    graph["graph_schema_version"] = 1
    for row in graph["entities"]:
        row.pop("provenance", None)
        row.pop("field_provenance", None)
    for row in graph["edges"]:
        row.pop("provenance", None)
    graph["active_tasks"] = [
        {"id": entity.eid, "name": entity.name, **entity.properties}
        for entity in model.get_entities_by_type("task") if entity.properties.get("status") == "active"
    ]
    return graph


def _build_candidate(original: dict, database: Path, *, bundle_version: int = 2) -> tuple[dict, dict]:
    if not isinstance(original, dict) or not isinstance(original.get("world_model"), dict):
        raise ValueError("snapshot must contain a world_model object")
    world = original["world_model"]
    model = WorldModelGraph.from_dict(world)
    with _backup_store(database) as s4:
        store_entities = list(s4.iter_entities())
        store_edges = list(s4.iter_edges())
        model.load_from_store(s4)
        merged = model.to_dict()
        again = WorldModelGraph.from_dict(merged)
        again.load_from_store(s4)
        if again.to_dict() != merged:
            raise ValueError("candidate recovery is not idempotent")
        if bundle_version == 1:
            merged = _legacy_graph_view(model)
    candidate = {**original, "world_model": merged}
    report = {
        "schema_version": bundle_version, "operation": "prepare_copy",
        "entities": _conflicts(world.get("entities", []), store_entities, merged["entities"], "entities", include_provenance=bundle_version >= 2),
        "edges": _conflicts(world.get("edges", []), store_edges, merged["edges"], "edges", include_provenance=bundle_version >= 2),
        "roundtrip_and_s4_merge_identical": True,
        "non_world_snapshot_sections_preserved": True,
        "source_files_replaced": False,
        "conflict_policy": "RST-01: newer comparable timestamps; S4 on ties/unknown; first snapshot duplicate on ties/unknown",
        "capture_limit": "SQLite backup is transactionally consistent; snapshot and DB are captured separately, not as a distributed transaction.",
        "history_limit": "Distinct identities are preserved. Conflicting old values are retained in the exact original backup, not all in the candidate graph.",
    }
    return candidate, report


def prepare_recovery(snapshot_path: Path, db_path: Path, output_dir: Path) -> dict:
    """Back up source data and prepare a reviewed candidate in a new directory."""
    snapshot_path, db_path = snapshot_path.resolve(strict=True), db_path.resolve(strict=True)
    if not snapshot_path.is_file() or not db_path.is_file() or snapshot_path == db_path:
        raise ValueError("snapshot and database must be distinct existing files")
    raw = snapshot_path.read_bytes()
    output = _new_directory(output_dir)
    # Preserve exact input bytes before parsing; partial failed bundles have no manifest.
    _write_new(output / "original.snapshot.json", raw)
    _sqlite_backup(db_path, output / "database.sqlite")
    if snapshot_path.read_bytes() != raw:
        raise RuntimeError("snapshot changed during backup; stop writers and prepare a new bundle")
    original = json.loads(raw)
    candidate, report = _build_candidate(original, output / "database.sqlite")
    _write_new(output / "candidate.snapshot.json", _json_bytes(candidate))
    _write_new(output / "report.json", _json_bytes(report))
    manifest = {
        "schema_version": 2, "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: {"sha256": _digest((output / name).read_bytes()), "bytes": (output / name).stat().st_size} for name in BUNDLE_FILES},
    }
    _write_new(output / "manifest.json", _json_bytes(manifest))
    verify_recovery(output)
    return report


def verify_recovery(bundle_dir: Path) -> dict:
    """Check file hashes, DB integrity and reconstruction against exact backups."""
    bundle = bundle_dir.resolve(strict=True)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] not in (1, 2) or manifest.get("status") != "complete":
        raise ValueError("unsupported or incomplete recovery bundle")
    if set(manifest.get("files", {})) != set(BUNDLE_FILES):
        raise ValueError("unexpected recovery bundle files")
    for name in BUNDLE_FILES:
        raw = (bundle / name).read_bytes()
        expected = manifest["files"][name]
        if len(raw) != expected["bytes"] or _digest(raw) != expected["sha256"]:
            raise ValueError(f"recovery bundle checksum mismatch: {name}")
    candidate, report = _build_candidate(json.loads((bundle / "original.snapshot.json").read_bytes()), bundle / "database.sqlite", bundle_version=manifest["schema_version"])
    if candidate != json.loads((bundle / "candidate.snapshot.json").read_bytes()):
        raise ValueError("candidate differs from reconstruction")
    if report != json.loads((bundle / "report.json").read_bytes()):
        raise ValueError("report differs from reconstruction")
    return report


def restore_recovery(bundle_dir: Path, output_dir: Path, *, variant: str = "original") -> dict:
    """Restore exact original or normalized candidate to a NEW directory only."""
    if variant not in {"original", "candidate"}:
        raise ValueError("restore variant must be original or candidate")
    bundle = bundle_dir.resolve(strict=True)
    verify_recovery(bundle)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    output = _new_directory(output_dir)
    expected = {}
    for source, target in ((f"{variant}.snapshot.json", "latest.json"), ("database.sqlite", "eva.db")):
        raw = (bundle / source).read_bytes()
        if _digest(raw) != manifest["files"][source]["sha256"]:
            raise ValueError("bundle changed after verification")
        _write_new(output / target, raw)
        expected[target] = _digest(raw)
        if _digest((output / target).read_bytes()) != expected[target]:
            raise ValueError("restored file checksum mismatch")
    receipt = {"schema_version": 1, "variant": variant, "verified": True, "sha256": expected}
    _write_new(output / "restore-receipt.json", _json_bytes(receipt))
    return receipt
