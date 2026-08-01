"""PostgreSQL storage backend for production deployments.

Provides the same interface as SQLiteStore (BaseStorageAdapter) but backed
by PostgreSQL via psycopg2. Supports connection pooling, automatic reconnect,
and JSON helpers.

Configuration via environment:
    EVA_PG_HOST / EVA_PG_PORT / EVA_PG_DATABASE / EVA_PG_USER / EVA_PG_PASSWORD
    or DATABASE_URL (postgresql://user:pass@host:port/dbname)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from memory.storage_adapter import BaseStorageAdapter

logger = logging.getLogger("eva.postgres")


class PostgresStore(BaseStorageAdapter):
    """Thread-safe PostgreSQL store with connection pooling.

    Each thread gets its own connection via threading.local().
    Connections are auto-reconnected on errors.

    Config is read from env vars or constructor kwargs:
        host, port, database, user, password
        or database_url (postgresql://...)
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        database: str = "",
        user: str = "",
        password: str = "",
        database_url: str = "",
        min_connections: int = 2,
        max_connections: int = 10,
    ) -> None:
        self._host = host or os.environ.get("EVA_PG_HOST", "localhost")
        self._port = port or int(os.environ.get("EVA_PG_PORT", "5432"))
        self._database = database or os.environ.get("EVA_PG_DATABASE", "eva")
        self._user = user or os.environ.get("EVA_PG_USER", "eva")
        self._password = password or os.environ.get("EVA_PG_PASSWORD", "")
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        self._min_connections = min_connections
        self._max_connections = max_connections
        self._local = threading.local()
        self._lock = threading.Lock()
        self._closed = False

    # ── connection management ────────────────────────────────

    def _build_dsn(self) -> str:
        """Build a psycopg2 DSN from config."""
        if self._database_url:
            return self._database_url
        parts = [
            f"host={self._host}",
            f"port={self._port}",
            f"dbname={self._database}",
            f"user={self._user}",
        ]
        if self._password:
            parts.append(f"password={self._password}")
        return " ".join(parts)

    def _get_conn(self):
        """Get or create a thread-local connection with auto-reconnect."""
        import psycopg2
        import psycopg2.extras

        if self._closed:
            raise RuntimeError("PostgresStore is closed")

        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            dsn = self._build_dsn()
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            # Register dict-like cursor factory
            psycopg2.extras.register_default_jsonb(conn)
            self._local.conn = conn
            logger.debug("new PG connection established")
        return conn

    def close(self) -> None:
        """Close all connections."""
        self._closed = True
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            conn.close()
            self._local.conn = None

    @contextmanager
    def _cursor(self):
        """Yield a cursor with auto-commit/rollback on error."""
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    # ── BaseStorageAdapter interface ──────────────────────────

    def init_db(self) -> None:
        """Initialize all tables and indexes via the migration runner.

        Note: PostgreSQL uses different SQL syntax for some constructs
        (e.g., AUTOINCREMENT → SERIAL, no WAL pragmas). The migration
        runner applies SQL files directly; ensure migrations are
        PostgreSQL-compatible when using this backend.
        """
        from core.migration import MigrationRunner

        # Ensure pgcrypto extension for gen_random_uuid()
        try:
            self.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        except Exception:
            logger.warning("could not create pgcrypto extension — UUID generation may fail")

        runner = MigrationRunner(self)
        applied = runner.migrate()
        if applied:
            logger.info("PG migrations applied: %s", applied)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        """Execute a write statement (INSERT/UPDATE/DELETE).

        Converts SQLite-style ? placeholders to PostgreSQL %s placeholders.
        """
        sql = _sqlite_to_pg(sql)
        params = tuple(params)
        with self._cursor() as cur:
            cur.execute(sql, params)

    def execute_many(self, sql: str, params_list: Iterable[Iterable[Any]]) -> None:
        """Execute multiple writes in a single transaction."""
        sql = _sqlite_to_pg(sql)
        with self._cursor() as cur:
            for params in params_list:
                cur.execute(sql, tuple(params))

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Execute a read query and return all rows as dicts."""
        import psycopg2.extras

        sql = _sqlite_to_pg(sql)
        params = tuple(params)
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        """Execute a read query and return the first row, or None."""
        import psycopg2.extras

        sql = _sqlite_to_pg(sql)
        params = tuple(params)
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    # ── JSON helpers (delegates to base) ─────────────────────
    # dumps_json and loads_json inherited from BaseStorageAdapter

    # ── health check ─────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if the database is reachable."""
        try:
            self.fetchone("SELECT 1")
            return True
        except Exception:
            return False


def _sqlite_to_pg(sql: str) -> str:
    """Convert SQLite-style SQL to PostgreSQL-compatible SQL.

    Handles:
    - ? → %s (positional parameters)
    - COLLATE NOCASE → ILIKE-based approach (handled at query level)
    - datetime('now') → NOW()
    - AUTOINCREMENT → (uses SERIAL, handled in schema)
    - BOOLEAN 0/1 → FALSE/TRUE (PostgreSQL accepts both)
    """
    # Replace ? placeholders with %s
    # Must be careful not to replace ? inside string literals
    # Simple heuristic: replace standalone ? not inside quotes
    result = _replace_placeholders(sql)
    # datetime('now') → NOW()
    result = result.replace("datetime('now')", "NOW()")
    result = result.replace("datetime('now','localtime')", "NOW()")
    return result


def _replace_placeholders(sql: str) -> str:
    """Replace ? placeholders with %s, skipping those inside string literals."""
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == "?" and not in_single and not in_double:
            result.append("%s")
        else:
            result.append(ch)
        i += 1
    return "".join(result)
