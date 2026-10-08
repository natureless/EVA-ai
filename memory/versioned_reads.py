"""SQLite S2/S3 revision counters updated inside the original write transaction."""

from uuid import uuid4
import re

from memory.sqlite_store import SQLiteStore
from memory.storage_adapter import BaseStorageAdapter


def install_memory_revisions(store: BaseStorageAdapter) -> None:
    if not isinstance(store, SQLiteStore):
        raise ValueError("versioned memory reads require SQLite")
    # Install before producers start. A persistent scope distinguishes rebuilt DBs.
    conn = store._get_conn()
    if conn.in_transaction:
        raise RuntimeError("cannot install revision tracking during a transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_view_revision (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            scope TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0))""")
        conn.execute(
            "INSERT OR IGNORE INTO memory_view_revision VALUES (1,?,0)",
            (f"memory_{uuid4().hex}",),
        )
        for table in ("working_memory", "long_term_memory"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_revision_{operation.lower()}
                    AFTER {operation} ON {table} BEGIN
                    UPDATE memory_view_revision SET revision=revision+1 WHERE singleton=1;
                    END""")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def revision_row(store: SQLiteStore):
    row = store.fetchone(
        "SELECT scope,revision FROM memory_view_revision WHERE singleton=1"
    )
    if (
        row is None
        or not isinstance(row["scope"], str)
        or re.fullmatch(r"memory_[0-9a-f]{32}", row["scope"]) is None
        or type(row["revision"]) is not int
        or row["revision"] < 0
    ):
        raise ValueError("invalid memory revision authority")
    return row
