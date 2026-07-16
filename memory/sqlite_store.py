import json
import sqlite3
import threading
from contextlib import contextmanager
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

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
            conn.execute("PRAGMA busy_timeout=2000")  # 2s busy wait
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def init_db(self) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL,
                timestamp TEXT NOT NULL, payload TEXT NOT NULL,
                correlation_id TEXT, status TEXT NOT NULL)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS episodic_memory (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL, summary TEXT NOT NULL,
                payload TEXT NOT NULL, importance REAL NOT NULL)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS semantic_memory (
                id TEXT PRIMARY KEY, concept TEXT NOT NULL,
                description TEXT NOT NULL, related_entities TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS traces (
                id TEXT PRIMARY KEY, loop_id TEXT NOT NULL,
                timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
                decision TEXT NOT NULL, agent TEXT NOT NULL,
                result_summary TEXT NOT NULL, duration_ms INTEGER NOT NULL)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS persona_profiles (
                persona_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                role_definition TEXT NOT NULL, tone_style TEXT NOT NULL,
                hard_constraints TEXT NOT NULL, soft_preferences TEXT NOT NULL,
                value_weights TEXT NOT NULL, version INTEGER NOT NULL,
                updated_at TEXT NOT NULL, confidence REAL NOT NULL,
                source_event_id TEXT)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS memory_items (
                id TEXT PRIMARY KEY, memory_type TEXT NOT NULL,
                content TEXT NOT NULL, source_event_id TEXT,
                salience REAL NOT NULL DEFAULT 0.0,
                confidence REAL NOT NULL DEFAULT 0.5, ttl_seconds INTEGER,
                embedding_ref TEXT, summary_ref TEXT,
                conflict_keys_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_accessed_at TEXT, status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}')""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS memory_links (
                id TEXT PRIMARY KEY, from_memory_id TEXT NOT NULL,
                to_memory_id TEXT NOT NULL, relation TEXT NOT NULL,
                strength REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL)""")
        cur.execute(
            """CREATE INDEX IF NOT EXISTS idx_memories_type_status
                ON memory_items(memory_type, status)""")
        cur.execute(
            """CREATE INDEX IF NOT EXISTS idx_memories_salience
                ON memory_items(salience)""")
        cur.execute(
            """CREATE INDEX IF NOT EXISTS idx_memories_source_event
                ON memory_items(source_event_id)""")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS graph_edges (
                source TEXT NOT NULL, target TEXT NOT NULL,
                relation TEXT NOT NULL, weight REAL NOT NULL,
                updated_at TEXT NOT NULL)""")

        # S2: Working Memory
        cur.execute(
            """CREATE TABLE IF NOT EXISTS working_memory (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 2,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wm_expires ON working_memory(expires_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wm_created ON working_memory(created_at)")

        # S3: Long-term Memory
        cur.execute(
            """CREATE TABLE IF NOT EXISTS long_term_memory (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                embedding_ref TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0.5,
                source_event_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ltm_category_status ON long_term_memory(category, status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ltm_importance ON long_term_memory(importance)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ltm_content ON long_term_memory(content COLLATE NOCASE)")

        # S4: World Model
        cur.execute(
            """CREATE TABLE IF NOT EXISTS world_entities (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_we_type ON world_entities(type)")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS world_edges (
                source TEXT NOT NULL, target TEXT NOT NULL,
                relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, target, relation))""")

        # Executor Audit
        cur.execute(
            """CREATE TABLE IF NOT EXISTS executor_audit (
                id TEXT PRIMARY KEY, executor_type TEXT NOT NULL,
                action TEXT NOT NULL, task_id TEXT NOT NULL,
                token_id TEXT NOT NULL DEFAULT '',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                result_summary TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, timestamp TEXT NOT NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_type_status ON executor_audit(executor_type, status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON executor_audit(timestamp)")
        conn.commit()

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
