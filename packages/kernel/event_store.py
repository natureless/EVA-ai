"""EVA-MVSC EventStore — append-only 事件持久化与确定性重放。

核心原则:
1. 事件不可修改 — 只能追加
2. 单调递增 sequence 编号
3. event_id 全局唯一
4. 按 subject_id 严格隔离
5. 事务内完成写入
6. 支持确定性事件重放

权威状态 = Snapshot(t₀) + Replay(Event[t₀:t])
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.contracts.events import EventEnvelope

logger = logging.getLogger("eva.kernel.event_store")


class VersionConflictError(Exception):
    """乐观锁版本冲突。"""
    pass


class DuplicateEventError(Exception):
    """重复事件ID。"""
    pass


class EventStore:
    """Append-only 事件存储。

    特性:
    - SQLite + WAL 模式
    - 单调递增 sequence
    - event_id 唯一约束
    - subject_id 隔离
    - correlation_id + causation_id 索引
    - 确定性重放
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS event_log (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT NOT NULL UNIQUE,
        event_type   TEXT NOT NULL,
        source       TEXT NOT NULL,
        timestamp    TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        subject_id   TEXT NOT NULL DEFAULT 'eva-001',
        session_id   TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        confidence   REAL NOT NULL DEFAULT 1.0,
        priority     REAL NOT NULL DEFAULT 0.0,
        sensitivity  TEXT NOT NULL DEFAULT 'internal',
        schema_version TEXT NOT NULL DEFAULT '1.0',
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_event_log_subject
        ON event_log(subject_id, sequence);

    CREATE INDEX IF NOT EXISTS idx_event_log_correlation
        ON event_log(correlation_id);

    CREATE INDEX IF NOT EXISTS idx_event_log_causation
        ON event_log(causation_id);

    CREATE INDEX IF NOT EXISTS idx_event_log_type
        ON event_log(event_type);

    CREATE TABLE IF NOT EXISTS event_checkpoint (
        subject_id    TEXT NOT NULL,
        last_sequence INTEGER NOT NULL,
        state_hash    TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (subject_id, last_sequence)
    );
    """

    def __init__(self, db_path: str | Path = "data/event_store.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库和Schema。"""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(self.SCHEMA)
        conn.commit()
        self._conn = conn
        logger.info("event store initialized at %s (WAL mode)", self._db_path)

    # ── 写入 ──────────────────────────────────────────────────

    def append(self, event: EventEnvelope) -> int:
        """追加事件到日志。

        Args:
            event: 要追加的事件信封

        Returns:
            分配的单调递增 sequence 号

        Raises:
            DuplicateEventError: event_id 已存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO event_log
                       (event_id, event_type, source, timestamp,
                        correlation_id, causation_id, subject_id, session_id,
                        payload_json, confidence, priority, sensitivity, schema_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.event_type,
                        event.source,
                        event.timestamp.isoformat(),
                        event.correlation_id,
                        event.causation_id,
                        event.subject_id,
                        event.session_id,
                        json.dumps(event.payload, ensure_ascii=False),
                        event.confidence,
                        event.priority,
                        event.sensitivity,
                        event.schema_version,
                    ),
                )
                conn.commit()

                # 获取分配的 sequence
                row = conn.execute(
                    "SELECT sequence FROM event_log WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                sequence = row[0]
                event.sequence = sequence
                return sequence

            except sqlite3.IntegrityError as e:
                if "UNIQUE" in str(e).upper():
                    raise DuplicateEventError(
                        f"event_id {event.event_id} already exists"
                    ) from e
                raise

    def append_batch(self, events: list[EventEnvelope]) -> list[int]:
        """批量追加事件（单事务）。"""
        with self._lock:
            conn = self._get_conn()
            sequences: list[int] = []
            try:
                for event in events:
                    conn.execute(
                        """INSERT INTO event_log
                           (event_id, event_type, source, timestamp,
                            correlation_id, causation_id, subject_id, session_id,
                            payload_json, confidence, priority, sensitivity, schema_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id,
                            event.event_type,
                            event.source,
                            event.timestamp.isoformat(),
                            event.correlation_id,
                            event.causation_id,
                            event.subject_id,
                            event.session_id,
                            json.dumps(event.payload, ensure_ascii=False),
                            event.confidence,
                            event.priority,
                            event.sensitivity,
                            event.schema_version,
                        ),
                    )
                    row = conn.execute(
                        "SELECT sequence FROM event_log WHERE event_id = ?",
                        (event.event_id,),
                    ).fetchone()
                    seq = row[0]
                    event.sequence = seq
                    sequences.append(seq)
                conn.commit()
                return sequences
            except Exception:
                conn.rollback()
                raise

    # ── 读取与重放 ────────────────────────────────────────────

    def replay(
        self,
        subject_id: str = "eva-001",
        from_sequence: int = 0,
        to_sequence: int | None = None,
    ) -> list[EventEnvelope]:
        """确定性事件重放。

        从 from_sequence 开始，返回有序事件列表。
        相同参数始终返回相同结果。
        """
        conn = self._get_conn()
        if to_sequence is not None:
            rows = conn.execute(
                """SELECT * FROM event_log
                   WHERE subject_id = ? AND sequence >= ? AND sequence <= ?
                   ORDER BY sequence ASC""",
                (subject_id, from_sequence, to_sequence),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM event_log
                   WHERE subject_id = ? AND sequence >= ?
                   ORDER BY sequence ASC""",
                (subject_id, from_sequence),
            ).fetchall()

        return [self._row_to_envelope({k: row[k] for k in row.keys()}) for row in rows]

    def get_by_correlation(self, correlation_id: str) -> list[EventEnvelope]:
        """获取同一关联ID的所有事件。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM event_log WHERE correlation_id = ? ORDER BY sequence ASC",
            (correlation_id,),
        ).fetchall()
        return [self._row_to_envelope({k: row[k] for k in row.keys()}) for row in rows]

    def get_causal_chain(self, event_id: str) -> list[EventEnvelope]:
        """追溯因果链（从当前事件回溯到根事件）。"""
        chain: list[EventEnvelope] = []
        current_id = event_id
        visited: set[str] = set()
        conn = self._get_conn()

        while current_id and current_id not in visited:
            visited.add(current_id)
            row = conn.execute(
                "SELECT * FROM event_log WHERE event_id = ?",
                (current_id,),
            ).fetchone()
            if row is None:
                break
            row_dict = {k: row[k] for k in row.keys()}
            chain.append(self._row_to_envelope(row_dict))
            current_id = row_dict.get("causation_id")

        return chain

    def get_latest_sequence(self, subject_id: str = "eva-001") -> int:
        """获取指定主体的最新 sequence 号。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT MAX(sequence) FROM event_log WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()
        return row[0] if row and row[0] else 0

    # ── 检查点 ────────────────────────────────────────────────

    def save_checkpoint(self, subject_id: str, last_sequence: int, state_hash: str) -> None:
        """保存检查点（状态哈希 + 序列号）。"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO event_checkpoint
                   (subject_id, last_sequence, state_hash)
                   VALUES (?, ?, ?)""",
                (subject_id, last_sequence, state_hash),
            )
            conn.commit()

    def get_latest_checkpoint(self, subject_id: str = "eva-001") -> dict | None:
        """获取最新检查点。"""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM event_checkpoint
               WHERE subject_id = ?
               ORDER BY last_sequence DESC LIMIT 1""",
            (subject_id,),
        ).fetchone()
        return {k: row[k] for k in row.keys()} if row else None

    # ── 验证 ──────────────────────────────────────────────────

    def verify_integrity(self, subject_id: str = "eva-001") -> dict[str, Any]:
        """验证事件日志完整性。

        Returns:
            {"ok": bool, "gaps": list[int], "total": int, "first": int, "last": int}
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT sequence FROM event_log WHERE subject_id = ? ORDER BY sequence ASC",
            (subject_id,),
        ).fetchall()

        sequences = [r[0] for r in rows]
        gaps: list[int] = []
        for i in range(len(sequences) - 1):
            if sequences[i + 1] != sequences[i] + 1:
                gaps.append(sequences[i] + 1)

        return {
            "ok": len(gaps) == 0,
            "gaps": gaps,
            "total": len(sequences),
            "first": sequences[0] if sequences else 0,
            "last": sequences[-1] if sequences else 0,
        }

    def compute_state_hash(self, subject_id: str = "eva-001") -> str:
        """计算从事件日志派生的状态哈希。

        用于验证重放确定性：相同事件 → 相同哈希。
        """
        events = self.replay(subject_id)
        data = json.dumps(
            [e.model_dump(mode="json") for e in events],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # ── 统计 ──────────────────────────────────────────────────

    def stats(self, subject_id: str = "eva-001") -> dict[str, Any]:
        """获取事件存储统计信息。"""
        conn = self._get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()[0]

        type_counts = conn.execute(
            """SELECT event_type, COUNT(*) as cnt
               FROM event_log WHERE subject_id = ?
               GROUP BY event_type ORDER BY cnt DESC LIMIT 20""",
            (subject_id,),
        ).fetchall()

        return {
            "total_events": total,
            "latest_sequence": self.get_latest_sequence(subject_id),
            "event_types": {r[0]: r[1] for r in type_counts},
            "db_path": str(self._db_path),
        }

    # ── 内部 ──────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._init_db()
        assert self._conn is not None
        return self._conn

    @staticmethod
    def _row_to_envelope(row: dict[str, Any]) -> EventEnvelope:
        """将数据库行转换为 EventEnvelope。"""
        payload = json.loads(row.get("payload_json", "{}"))
        return EventEnvelope(
            event_id=row["event_id"],
            event_type=row["event_type"],
            source=row["source"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            correlation_id=row["correlation_id"],
            causation_id=row.get("causation_id"),
            subject_id=row.get("subject_id", "eva-001"),
            session_id=row.get("session_id"),
            sequence=row["sequence"],
            payload=payload,
            confidence=float(row.get("confidence", 1.0)),
            priority=float(row.get("priority", 0.0)),
            sensitivity=row.get("sensitivity", "internal"),
            schema_version=row.get("schema_version", "1.0"),
        )

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None
