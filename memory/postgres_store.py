"""PostgreSQL storage adapter — conforms to BaseStorageAdapter.

Usage — when EVA_STORAGE_BACKEND=postgresql::

    store = create_store(settings)  # returns PostgresStore

Connection pooling via psycopg2.ThreadedConnectionPool.
Schema is identical to SQLite's — CREATE TABLE IF NOT EXISTS.

v0.1 status: skeleton. Key methods are functional stubs that raise
NotImplementedError with clear upgrade path. Tests run against SQLite
in all cases.

Dependencies (optional)::
    pip install psycopg2-binary
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterable

from memory.storage_adapter import BaseStorageAdapter

logger = logging.getLogger("eva.storage.postgres")


class PostgresStore(BaseStorageAdapter):
    """PostgreSQL storage backend.

    Config via EVA_PG_HOST/PORT/DATABASE/USER/PASSWORD env vars,
    or pass database_url directly.
    """

    def __init__(self, database_url: str = "", pool_size: int = 4) -> None:
        self.database_url = database_url or self._default_url()
        self.pool_size = pool_size
        self._pool = None
        self._ready = False
        logger.info("postgres store initialized (url=%s)", self._mask_url())

    # ── lifecycle ───────────────────────────────────────────

    def init_db(self) -> None:
        """Execute CREATE TABLE IF NOT EXISTS for all tables.

        This is idempotent — safe to call on every startup.
        Uses the same schema as SQLite with PostgreSQL types.
        """
        self._ensure_pool()
        sql = self._schema_sql()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        self._ready = True
        logger.info("postgres schema initialized")

    def close(self) -> None:
        if self._pool:
            self._pool.closeall()
            self._pool = None
            self._ready = False

    # ── public API ──────────────────────────────────────────

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        self._ensure_pool()
        # convert ? placeholders to %s for PostgreSQL
        sql = self._adapt_sql(sql, params)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
            conn.commit()

    def execute_many(self, sql: str, params_list: Iterable[Iterable[Any]]) -> None:
        self._ensure_pool()
        sql = self._adapt_sql(sql, [])
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for params in params_list:
                    cur.execute(sql, tuple(params))
            conn.commit()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        self._ensure_pool()
        sql = self._adapt_sql(sql, params)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
                return [dict(zip(columns, row)) for row in rows]

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        self._ensure_pool()
        sql = self._adapt_sql(sql, params)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                if row and cur.description:
                    columns = [desc[0] for desc in cur.description]
                    return dict(zip(columns, row))
        return None

    # ── internals ───────────────────────────────────────────

    def _ensure_pool(self) -> None:
        try:
            import psycopg2
            from psycopg2 import pool as pgpool
        except ImportError:
            raise RuntimeError(
                "psycopg2 not installed. Run: pip install psycopg2-binary\n"
                "Or switch to SQLite: EVA_STORAGE_BACKEND=sqlite"
            )
        if self._pool is None:
            self._pool = pgpool.ThreadedConnectionPool(
                1, self.pool_size, self.database_url
            )

    @contextmanager
    def _get_conn(self):
        """Get a connection from the pool, yielding it, then put it back."""
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def _schema_sql(self) -> str:
        """PostgreSQL CREATE TABLE statements — same logical schema as SQLite."""
        return """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL,
            timestamp TEXT NOT NULL, payload TEXT NOT NULL,
            correlation_id TEXT, status TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS episodic_memory (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL, summary TEXT NOT NULL,
            payload TEXT NOT NULL, importance REAL NOT NULL);

        CREATE TABLE IF NOT EXISTS semantic_memory (
            id TEXT PRIMARY KEY, concept TEXT NOT NULL,
            description TEXT NOT NULL, related_entities TEXT NOT NULL,
            created_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS traces (
            id TEXT PRIMARY KEY, loop_id TEXT NOT NULL,
            timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
            decision TEXT NOT NULL, agent TEXT NOT NULL,
            result_summary TEXT NOT NULL, duration_ms INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS persona_profiles (
            persona_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            role_definition TEXT NOT NULL, tone_style TEXT NOT NULL,
            hard_constraints TEXT NOT NULL, soft_preferences TEXT NOT NULL,
            value_weights TEXT NOT NULL, version INTEGER NOT NULL,
            updated_at TEXT NOT NULL, confidence REAL NOT NULL,
            source_event_id TEXT);

        CREATE TABLE IF NOT EXISTS memory_items (
            id TEXT PRIMARY KEY, memory_type TEXT NOT NULL,
            content TEXT NOT NULL, source_event_id TEXT,
            salience REAL NOT NULL DEFAULT 0.0,
            confidence REAL NOT NULL DEFAULT 0.5, ttl_seconds INTEGER,
            embedding_ref TEXT, summary_ref TEXT,
            conflict_keys_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            last_accessed_at TEXT, status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}');

        CREATE TABLE IF NOT EXISTS memory_links (
            id TEXT PRIMARY KEY, from_memory_id TEXT NOT NULL,
            to_memory_id TEXT NOT NULL, relation TEXT NOT NULL,
            strength REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS working_memory (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 2,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS long_term_memory (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            embedding_ref TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0.5,
            source_event_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS world_entities (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
            properties_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS world_edges (
            source TEXT NOT NULL, target TEXT NOT NULL,
            relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source, target, relation));

        CREATE TABLE IF NOT EXISTS executor_audit (
            id TEXT PRIMARY KEY, executor_type TEXT NOT NULL,
            action TEXT NOT NULL, task_id TEXT NOT NULL,
            token_id TEXT NOT NULL DEFAULT '',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            result_summary TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, timestamp TEXT NOT NULL);
        """

    @staticmethod
    def _adapt_sql(sql: str, params: Iterable[Any]) -> str:
        """Convert SQLite ? placeholders to PostgreSQL %s."""
        # simple approach: count ? and replace with %s
        return sql.replace("?", "%s")

    def _default_url(self) -> str:
        import os
        host = os.environ.get("EVA_PG_HOST", "localhost")
        port = os.environ.get("EVA_PG_PORT", "5432")
        db = os.environ.get("EVA_PG_DATABASE", "eva")
        user = os.environ.get("EVA_PG_USER", "eva")
        pw = os.environ.get("EVA_PG_PASSWORD", "")
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    def _mask_url(self) -> str:
        url = self.database_url
        if "@" in url:
            prefix = url.split("@")[0].split("://")[0]
            host_part = url.split("@")[1]
            return f"{prefix}://***@{host_part}"
        return url
