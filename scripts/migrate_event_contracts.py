"""Upgrade/backfill event envelopes in a new SQLite copy; never migrate the source.

python scripts/migrate_event_contracts.py --source data/eva.db \
    --output data/event-contracts/evt01-copy.db --report artifacts/evt01-migration.json
"""

from collections import Counter
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from event.codec import decode_event, encode_event  # noqa: E402 — standalone script path bootstrap
from memory.sqlite_store import SQLiteStore  # noqa: E402


def backfill(store: SQLiteStore, batch_size: int = 500) -> dict:
    """Only add envelopes for valid supported legacy rows, in bounded batches.

    Invalid or archive-only types remain unmodified and are counted explicitly.
    Existing contracts are validated; they are not rewritten or silently upgraded.
    """
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    counts = Counter()
    after = None
    while True:
        rows = store.fetchall("SELECT rowid AS _cursor, * FROM events ORDER BY rowid LIMIT ?", (batch_size,)) if after is None else store.fetchall(
            "SELECT rowid AS _cursor, * FROM events WHERE rowid > ? ORDER BY rowid LIMIT ?", (after, batch_size))
        if not rows:
            break
        updates = []
        for row in rows:
            after = row.pop("_cursor")
            counts["scanned"] += 1
            try:
                event = decode_event(row)
                contract = encode_event(event)
            except (ValueError, TypeError, AttributeError):
                counts["unsupported_or_invalid"] += 1
                continue
            if row.get("event_contract") is None:
                updates.append((contract, row["id"]))
                counts["backfilled"] += 1
            else:
                counts["existing_valid"] += 1
        store.execute_many("UPDATE events SET event_contract = ? WHERE id = ? AND event_contract IS NULL", updates)
    return {key: counts[key] for key in ("scanned", "backfilled", "existing_valid", "unsupported_or_invalid")}


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def migrate_copy(source: Path, output: Path) -> dict:
    source, output = source.resolve(strict=True), output.resolve()
    before = file_hash(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation rejects existing files, the source itself, and aliases.
    with output.open("xb"):
        pass
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as incoming:
        with closing(sqlite3.connect(output)) as destination:
            incoming.backup(destination)
    store = SQLiteStore(output)
    try:
        store.init_db()
        counts = backfill(store)
    finally:
        store.close()
    after = file_hash(source)
    return {"schema_version": 1, "work_item": "EVT-01", **counts,
            "status": "partial" if counts["unsupported_or_invalid"] else "complete",
            "source_sha256_before": before, "source_sha256_after": after,
            "source_file_unchanged": before == after,
            "scope": "new SQLite copy; only envelope column backfill, no event execution or source writes"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() in {args.source.resolve(), args.output.resolve()}:
        parser.error("report must not overwrite either database")
    result = migrate_copy(args.source, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if not result["source_file_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
