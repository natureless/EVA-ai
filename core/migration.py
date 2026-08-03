"""Simple file-based migration runner for EVA.

Reads numbered .sql files from the migrations/ directory and applies them
in order. Tracks applied migrations in a `_migrations` table.

Usage:
    runner = MigrationRunner(store)
    runner.migrate()  # applies any unapplied migrations
"""

import logging
from pathlib import Path

from memory.storage_adapter import BaseStorageAdapter

logger = logging.getLogger("eva.migration")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class MigrationRunner:
    """Reads and applies SQL migration files in numbered order."""

    def __init__(self, store: BaseStorageAdapter) -> None:
        self.store = store

    def migrate(self) -> list[str]:
        """Apply all unapplied migrations. Returns list of applied migration names."""
        self._ensure_tracking_table()
        applied = self._applied_migrations()
        pending = self._pending_migrations(applied)

        for mig in pending:
            logger.info("applying migration: %s", mig["name"])
            try:
                for stmt in _split_sql(mig["sql"]):
                    if stmt.strip():
                        self.store.execute(stmt)
                self.store.execute(
                    "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                    (mig["name"], mig["name"]),
                )
                logger.info("migration applied: %s", mig["name"])
            except Exception as e:
                # Gracefully handle "already exists" errors (e.g., duplicate column)
                err_msg = str(e).lower()
                if "duplicate column" in err_msg or "already exists" in err_msg:
                    logger.info("migration %s already applied (column exists), marking as done", mig["name"])
                    self.store.execute(
                        "INSERT OR IGNORE INTO _migrations (name, applied_at) VALUES (?, ?)",
                        (mig["name"], mig["name"]),
                    )
                else:
                    logger.exception("migration failed: %s", mig["name"])
                    raise

        return [m["name"] for m in pending]

    # ── internal ──────────────────────────────────────────────

    def _ensure_tracking_table(self) -> None:
        self.store.execute(
            """CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )

    def _applied_migrations(self) -> set[str]:
        try:
            rows = self.store.fetchall("SELECT name FROM _migrations ORDER BY name")
            return {r["name"] for r in rows}
        except Exception:
            logger.warning("could not read _migrations table — treating as empty", exc_info=True)
            return set()

    def _pending_migrations(
        self, applied: set[str]
    ) -> list[dict[str, str]]:
        if not MIGRATIONS_DIR.exists():
            return []

        files = sorted(
            p for p in MIGRATIONS_DIR.iterdir()
            if p.suffix == ".sql" and p.name[0].isdigit()
        )

        pending = []
        for f in files:
            if f.name not in applied:
                sql = f.read_text(encoding="utf-8").strip()
                if sql:
                    pending.append({"name": f.name, "sql": sql})
        return pending


def _split_sql(sql: str) -> list[str]:
    """Split multi-statement SQL into individual statements.

    Handles CREATE TRIGGER blocks (which contain internal semicolons)
    by buffering lines between CREATE TRIGGER and END;.
    """
    statements = []
    buffer: list[str] = []
    in_trigger = False

    for raw in sql.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue
        # skip pure comment blocks
        lines = [
            line
            for line in stmt.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        if not lines:
            continue

        if in_trigger:
            buffer.append(stmt)
            if stmt.strip().upper().startswith("END"):
                statements.append(";".join(buffer))
                buffer = []
                in_trigger = False
        elif any(line.strip().upper().startswith("CREATE TRIGGER") for line in lines) and "END" not in stmt.upper():
            buffer.append(stmt)
            in_trigger = True
        else:
            statements.append(stmt)

    if buffer:
        statements.append(";".join(buffer))

    return statements
