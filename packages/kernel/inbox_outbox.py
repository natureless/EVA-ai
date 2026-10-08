"""Durable inbox/outbox boundary for restart-safe event delivery.

The existing in-memory EventBus remains the low-latency consumer.  This module
provides the durable admission and delivery contract that can be attached to a
consumer without claiming exactly-once execution: inbox and outbox transitions
are idempotent, leases recover after a crash, and external side effects still
need their own idempotency key.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4

from packages.contracts.events import EventEnvelope


@dataclass(frozen=True)
class InboxClaim:
    event: EventEnvelope
    attempts: int


@dataclass(frozen=True)
class OutboxClaim:
    outbox_id: str
    event_id: str
    topic: str
    subject_id: str
    payload: dict[str, Any]
    attempts: int


class DurableInboxOutbox:
    """SQLite-backed inbox and outbox with lease-based recovery."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS durable_inbox (
        event_id       TEXT PRIMARY KEY,
        subject_id     TEXT NOT NULL,
        event_contract TEXT NOT NULL,
        status         TEXT NOT NULL CHECK(status IN ('pending','processing','completed','failed')),
        attempts       INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        available_at   REAL NOT NULL,
        locked_at      REAL,
        last_error     TEXT,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_durable_inbox_claim
        ON durable_inbox(status, available_at, created_at);

    CREATE TABLE IF NOT EXISTS durable_outbox (
        outbox_id    TEXT PRIMARY KEY,
        event_id     TEXT NOT NULL,
        subject_id   TEXT NOT NULL,
        topic        TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status       TEXT NOT NULL CHECK(status IN ('pending','processing','sent','failed')),
        attempts     INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        available_at REAL NOT NULL,
        locked_at    REAL,
        last_error   TEXT,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL,
        UNIQUE(event_id, topic)
    );
    CREATE INDEX IF NOT EXISTS idx_durable_outbox_claim
        ON durable_outbox(status, available_at, created_at);
    """

    def __init__(self, db_path: str | Path = "data/inbox_outbox.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def enqueue_inbox(self, event: EventEnvelope, *, now: float | None = None) -> bool:
        """Durably admit an event once; duplicate event IDs are ignored."""
        event = EventEnvelope.model_validate(event.model_dump())
        stamp = time.time() if now is None else now
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO durable_inbox
                   (event_id, subject_id, event_contract, status, available_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    event.event_id,
                    event.subject_id,
                    event.model_dump_json(),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def claim_inbox(
        self,
        *,
        worker_id: str,
        limit: int = 1,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> list[InboxClaim]:
        """Claim available or expired work and extend its lease atomically."""
        if not worker_id:
            raise ValueError("worker_id is required")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stamp = time.time() if now is None else now
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT event_id, event_contract, attempts
                       FROM durable_inbox
                       WHERE available_at <= ?
                         AND (status = 'pending' OR (status = 'processing' AND locked_at <= ?))
                       ORDER BY created_at, event_id LIMIT ?""",
                    (stamp, stamp, limit),
                ).fetchall()
                claims: list[InboxClaim] = []
                for row in rows:
                    attempts = int(row["attempts"]) + 1
                    self._conn.execute(
                        """UPDATE durable_inbox
                           SET status='processing', attempts=?, locked_at=?, updated_at=?
                           WHERE event_id=?""",
                        (attempts, stamp + lease_seconds, stamp, row["event_id"]),
                    )
                    claims.append(
                        InboxClaim(
                            event=EventEnvelope.model_validate_json(row["event_contract"]),
                            attempts=attempts,
                        )
                    )
                self._conn.commit()
                return claims
            except Exception:
                self._conn.rollback()
                raise

    def complete_inbox(self, event_id: str, *, now: float | None = None) -> bool:
        """Mark inbox work complete; repeating completion is harmless."""
        stamp = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM durable_inbox WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "completed":
                return True
            if row["status"] != "processing":
                return False
            self._conn.execute(
                """UPDATE durable_inbox
                   SET status='completed', locked_at=NULL, updated_at=?
                   WHERE event_id=? AND status='processing'""",
                (stamp, event_id),
            )
            self._conn.commit()
            return True

    def fail_inbox(
        self,
        event_id: str,
        error: str,
        *,
        retry_at: float | None = None,
        now: float | None = None,
    ) -> bool:
        """Record failure and either schedule a retry or terminally fail."""
        if not error:
            raise ValueError("error is required")
        stamp = time.time() if now is None else now
        status = "pending" if retry_at is not None else "failed"
        available = stamp if retry_at is None else retry_at
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE durable_inbox
                   SET status=?, available_at=?, locked_at=NULL, last_error=?, updated_at=?
                   WHERE event_id=? AND status='processing'""",
                (status, available, error[:2000], stamp, event_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def enqueue_outbox(
        self,
        *,
        event_id: str,
        subject_id: str,
        topic: str,
        payload: dict[str, Any],
        now: float | None = None,
    ) -> str | None:
        """Add one externally deliverable message per ``event_id``/``topic``."""
        if not event_id or not subject_id or not topic:
            raise ValueError("event_id, subject_id and topic are required")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("outbox payload must be finite JSON") from exc
        stamp = time.time() if now is None else now
        outbox_id = f"out_{uuid4().hex}"
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO durable_outbox
                   (outbox_id, event_id, subject_id, topic, payload_json, status,
                    available_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (outbox_id, event_id, subject_id, topic, encoded, stamp, stamp, stamp),
            )
            self._conn.commit()
            return outbox_id if cursor.rowcount == 1 else None

    def claim_outbox(
        self,
        *,
        worker_id: str,
        limit: int = 1,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> list[OutboxClaim]:
        """Claim pending or expired outbox delivery attempts."""
        if not worker_id:
            raise ValueError("worker_id is required")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stamp = time.time() if now is None else now
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT * FROM durable_outbox
                       WHERE available_at <= ?
                         AND (status='pending' OR (status='processing' AND locked_at <= ?))
                       ORDER BY created_at, outbox_id LIMIT ?""",
                    (stamp, stamp, limit),
                ).fetchall()
                claims: list[OutboxClaim] = []
                for row in rows:
                    attempts = int(row["attempts"]) + 1
                    self._conn.execute(
                        """UPDATE durable_outbox
                           SET status='processing', attempts=?, locked_at=?, updated_at=?
                           WHERE outbox_id=?""",
                        (attempts, stamp + lease_seconds, stamp, row["outbox_id"]),
                    )
                    claims.append(
                        OutboxClaim(
                            outbox_id=row["outbox_id"],
                            event_id=row["event_id"],
                            topic=row["topic"],
                            subject_id=row["subject_id"],
                            payload=json.loads(row["payload_json"]),
                            attempts=attempts,
                        )
                    )
                self._conn.commit()
                return claims
            except Exception:
                self._conn.rollback()
                raise

    def mark_outbox_sent(self, outbox_id: str, *, now: float | None = None) -> bool:
        """Mark delivery complete; repeated acknowledgement is idempotent."""
        stamp = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM durable_outbox WHERE outbox_id=?", (outbox_id,)
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "sent":
                return True
            if row["status"] != "processing":
                return False
            self._conn.execute(
                """UPDATE durable_outbox
                   SET status='sent', locked_at=NULL, updated_at=?
                   WHERE outbox_id=? AND status='processing'""",
                (stamp, outbox_id),
            )
            self._conn.commit()
            return True

    def fail_outbox(
        self,
        outbox_id: str,
        error: str,
        *,
        retry_at: float | None = None,
        now: float | None = None,
    ) -> bool:
        """Record failed delivery and optionally make it retryable."""
        if not error:
            raise ValueError("error is required")
        stamp = time.time() if now is None else now
        status = "pending" if retry_at is not None else "failed"
        available = stamp if retry_at is None else retry_at
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE durable_outbox
                   SET status=?, available_at=?, locked_at=NULL, last_error=?, updated_at=?
                   WHERE outbox_id=? AND status='processing'""",
                (status, available, error[:2000], stamp, outbox_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for table, prefix in (("durable_inbox", "inbox"), ("durable_outbox", "outbox")):
                rows = self._conn.execute(
                    f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status"
                ).fetchall()
                for row in rows:
                    result[f"{prefix}_{row['status']}"] = int(row["count"])
            return result

    def close(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()


__all__ = ["DurableInboxOutbox", "InboxClaim", "OutboxClaim"]
