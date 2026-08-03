import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from memory.storage_adapter import BaseStorageAdapter


class SQLiteStore(BaseStorageAdapter):
    """Thread-safe SQLite store with WAL mode and connection pooling.

    WAL (Write-Ahead Logging) allows concurrent reads during writes,
    dramatically reducing contention. Each thread gets its own connection
    via threading.local() to avoid SQLite's single-writer limitation.
    """
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            # Connections remain thread-local during normal operation, but
            # check_same_thread=False lets the main shutdown path close every
            # worker connection after those workers have stopped.
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
            conn.execute("PRAGMA busy_timeout=2000")  # 2s busy wait
            self._local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return self._local.conn  # type: ignore[no-any-return]

    def close(self) -> None:
        with self._connections_lock:
            connections = self._connections
            self._connections = []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.conn = None

    def init_db(self) -> None:
        from core.migration import MigrationRunner
        runner = MigrationRunner(self)
        applied = runner.migrate()
        if applied:
            import logging
            logging.getLogger("eva.store").info("migrations applied: %s", applied)

    # ── public API ──────────────────────────────────────────

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        conn = self._get_conn()
        conn.execute(sql, tuple(params))
        conn.commit()

    def execute_many(self, sql: str, params_list: Iterable[Iterable[Any]]) -> None:
        """Batch execute: all statements in a single transaction."""
        conn = self._get_conn()
        cur = conn.cursor()
        for params in params_list:
            cur.execute(sql, tuple(params))
        conn.commit()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def loads_json(value: str) -> Any:
        return json.loads(value)
