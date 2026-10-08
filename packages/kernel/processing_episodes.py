"""Durable processing drafts and immutable canonical-receipt Episodes.

World/memory/worker effects are outside this transaction. Recovery uses validated
persisted receipts when available, otherwise seals unknown outcomes. It never
replays the original event or an external action.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import time
from threading import Lock
from uuid import uuid4
from typing import Literal
from memory.tool_receipts import ToolReceipt, UnresolvedToolCall, read_tool_receipt

from memory.episodes import (
    EpisodeAction,
    EpisodeRecord,
    EpisodeResult,
    StateReference,
    read_episode,
)
from packages.kernel.episode_store import EpisodeStore
from packages.kernel.tool_reconciliation import (
    SCHEMA as OBSERVATION_SCHEMA,
    ToolReconciliation,
)


def world_reference(snapshot):
    encoded = json.dumps(
        snapshot,
        ensure_ascii=True,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return StateReference(
        version=None, integrity_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    )


class ProcessingEpisodes(ToolReconciliation, EpisodeStore):
    DRAFT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS episode_processing (
        event_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS episode_receipt_outbox (
        event_id TEXT PRIMARY KEY REFERENCES episodes(event_id),
        task_id TEXT NOT NULL UNIQUE,
        schema_version INTEGER NOT NULL,
        receipt_json TEXT NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1))
    );
    CREATE INDEX IF NOT EXISTS idx_episode_receipts_pending
        ON episode_receipt_outbox(delivered, event_id);
    CREATE TABLE IF NOT EXISTS episode_tool_receipts (
        action_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL UNIQUE,
        record_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_episode_tools_event ON episode_tool_receipts(event_id);
    """

    def __init__(self, db_path, *, subject_id="eva-001", file_verifier=None):
        super().__init__(db_path)
        self.subject_id = subject_id
        self.file_verifier = file_verifier
        self._closed = False
        self._receipt_sink = None
        self._delivery_lock = Lock()
        self._delivery_errors = 0
        try:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(self.DRAFT_SCHEMA)
            self._conn.executescript(OBSERVATION_SCHEMA)
        except BaseException:
            self._conn.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("processing Episode store is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    @staticmethod
    def _encode(record):
        record = read_episode(record.as_record())
        encoded = json.dumps(
            record.as_record(),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("processing Episode exceeds record limit")
        return encoded

    def _draft(self, event_id):
        row = self._conn.execute(
            "SELECT record_json FROM episode_processing WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        record = read_episode(json.loads(row[0]))
        if (
            record.event_id != event_id
            or record.schema_version != 2
            or record.correlation_id != record.metadata.get("task_id")
            or not isinstance(record.metadata.get("loop_id"), str)
            or not record.metadata.get("loop_id")
            or record.result.status != "pending"
            or record.completed_at is not None
        ):
            raise ValueError("inconsistent processing draft")
        return record

    def _save(self, record):
        self._conn.execute(
            "UPDATE episode_processing SET record_json=? WHERE event_id=?",
            (self._encode(record), record.event_id),
        )

    def begin(self, event, loop_id, snapshot):
        if event.correlation_id and len(event.correlation_id) > 256:
            raise ValueError("processing task ID exceeds record limit")
        record = EpisodeRecord(
            schema_version=2,
            event_id=event.id,
            event_type=event.type,
            source=event.source,
            subject_id=self.subject_id,
            correlation_id=event.correlation_id,
            state_before=world_reference(snapshot),
            metadata={
                "task_id": event.correlation_id,
                "loop_id": loop_id,
                "state_reference_kind": "world_snapshot_unversioned",
                "action_scope": "agent_invocation_only",
            },
        )
        encoded = self._encode(record)
        with self._transaction():
            if (
                self._conn.execute(
                    "SELECT 1 FROM episodes WHERE event_id=?", (event.id,)
                ).fetchone()
                or self._conn.execute(
                    "SELECT 1 FROM episode_processing WHERE event_id=?", (event.id,)
                ).fetchone()
            ):
                return False
            self._conn.execute(
                "INSERT INTO episode_processing VALUES (?,?)", (event.id, encoded)
            )
        return True

    def action_started(self, event_id, loop_id, agent, *, intent_binding=None):
        with self._transaction():
            record = self._draft(event_id)
            if record is None or record.metadata.get("loop_id") != loop_id:
                return
            if record.actions:
                raise ValueError("agent invocation already recorded")
            linked = {}
            if intent_binding is not None:
                if set(intent_binding) != {
                    "action_id",
                    "state_version",
                    "request_hash",
                }:
                    raise ValueError("invalid agent intent binding")
                linked = {
                    "action_id": intent_binding["action_id"],
                    "request_hash": intent_binding["request_hash"],
                    "tool_id": "runtime:agent_invocation",
                }
                record.metadata["agent_intent"] = dict(intent_binding)
            record.actions = [
                EpisodeAction(
                    **linked,
                    kind="agent_invocation",
                    status="started",
                    started_at=datetime.now(timezone.utc),
                )
            ]
            record.metadata["selected_agent"] = agent[:128]
            self._save(record)

    def action_finished(self, event_id, loop_id, result):
        with self._transaction():
            record = self._draft(event_id)
            if (
                record is None
                or record.metadata.get("loop_id") != loop_id
                or not record.actions
            ):
                return
            record.actions[0].status = "completed" if result.ok else "unknown"
            # An unsuccessful agent return does not prove that all nested tool effects failed.
            record.actions[0].completed_at = datetime.now(timezone.utc)
            self._save(record)

    def checkpoint(self, event_id, loop_id, snapshot):
        reference = world_reference(snapshot)
        with self._transaction():
            record = self._draft(event_id)
            if record is not None and record.metadata.get("loop_id") == loop_id:
                record.state_after = reference
                self._save(record)

    def _tool_receipt(self, action_id):
        row = self._conn.execute(
            "SELECT action_id,event_id,receipt_id,CASE WHEN length(CAST(record_json AS BLOB))<=8192 THEN record_json END AS record_json FROM episode_tool_receipts WHERE action_id=?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise ValueError("tool receipt not found")
        receipt = read_tool_receipt(row["record_json"])
        if (
            receipt.action_id != row["action_id"]
            or receipt.event_id != row["event_id"]
            or receipt.receipt_id != row["receipt_id"]
        ):
            raise ValueError("tool receipt identity mismatch")
        return receipt

    def _save_tool_receipt(self, receipt):
        encoded = receipt.model_dump_json()
        read_tool_receipt(encoded)
        self._conn.execute(
            "UPDATE episode_tool_receipts SET record_json=? WHERE action_id=?",
            (encoded, receipt.action_id),
        )

    def tool_started(self, event_id, loop_id, tool_id, args, *, parent_action_id=""):
        encoded_args = json.dumps(
            args,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(encoded_args.encode("utf-8")) > 1_048_576:
            raise ValueError("tool request exceeds hash input limit")
        request_hash = hashlib.sha256(encoded_args.encode("utf-8")).hexdigest()
        with self._transaction():
            record = self._draft(event_id)
            if (
                record is None
                or record.metadata.get("loop_id") != loop_id
                or not record.actions
            ):
                raise ValueError("tool call has no active owned processing draft")
            if len(record.actions) >= 64:
                raise ValueError("processing action limit reached")
            if any(
                a.kind == "tool_invocation"
                and a.tool_id == tool_id
                and a.request_hash == request_hash
                and a.status in {"started", "unknown"}
                for a in record.actions
            ):
                raise UnresolvedToolCall(
                    "previous identical tool outcome is unresolved"
                )
            parent_action_id = parent_action_id or record.actions[0].action_id
            if not any(
                a.action_id == parent_action_id and a.status == "started"
                for a in record.actions
            ):
                raise ValueError("tool call parent is not active")
            started_at = datetime.now(timezone.utc)
            action = EpisodeAction(
                kind="tool_invocation",
                tool_id=tool_id,
                status="started",
                request_hash=request_hash,
                receipt_id=f"tr_{uuid4().hex}",
                started_at=started_at,
            )
            receipt = ToolReceipt(
                action_id=action.action_id,
                receipt_id=action.receipt_id,
                event_id=event_id,
                loop_id=loop_id,
                parent_action_id=parent_action_id,
                tool_id=tool_id,
                request_hash=action.request_hash,
                started_at=started_at,
            )
            encoded = receipt.model_dump_json()
            read_tool_receipt(encoded)
            self._conn.execute(
                "INSERT INTO episode_tool_receipts VALUES (?,?,?,?)",
                (action.action_id, event_id, action.receipt_id, encoded),
            )
            self._capture_file_intent(receipt, args)
            record.actions.append(action)
            record.metadata["action_scope"] = "agent_and_tool_entries"
            self._save(record)
            return action.action_id

    def tool_finished(
        self, event_id, loop_id, action_id, *, returned_ok, observation_kind
    ):
        with self._transaction():
            receipt = self._tool_receipt(action_id)
            if receipt.event_id != event_id or receipt.loop_id != loop_id:
                raise ValueError("tool observation owner mismatch")
            record = self._draft(event_id)
            if receipt.status != "started" or record is None:
                return False
            if record.metadata.get("loop_id") != loop_id:
                raise ValueError("tool observation draft owner mismatch")
            receipt = ToolReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": "completed" if returned_ok is True else "unknown",
                    "returned_ok": returned_ok,
                    "observation_kind": observation_kind,
                    "finished_at": datetime.now(timezone.utc),
                }
            )
            action = next((a for a in record.actions if a.action_id == action_id), None)
            if action is None or action.receipt_id != receipt.receipt_id:
                raise ValueError("tool observation does not match draft action")
            action.status = receipt.status
            action.completed_at = receipt.finished_at
            self._save_tool_receipt(receipt)
            self._save(record)
            return True

    def tool_receipts(self, event_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT action_id FROM episode_tool_receipts WHERE event_id=? ORDER BY rowid LIMIT 64",
                (event_id,),
            ).fetchall()
            return [self._tool_receipt(row[0]) for row in rows]

    def _seal(self, record):
        for action in record.actions:
            if action.kind != "tool_invocation":
                continue
            receipt = self._tool_receipt(action.action_id)
            if (
                receipt.event_id != record.event_id
                or receipt.loop_id != record.metadata["loop_id"]
                or receipt.receipt_id != action.receipt_id
                or receipt.tool_id != action.tool_id
                or receipt.request_hash != action.request_hash
            ):
                raise ValueError("tool receipt does not match sealing Episode")
            if receipt.status == "started":
                receipt = ToolReceipt.model_validate(
                    {
                        **receipt.model_dump(),
                        "status": "unknown",
                        "observation_kind": "seal_unknown",
                        "sealed_at": record.completed_at,
                    }
                )
                self._save_tool_receipt(receipt)
            action.status = receipt.status
            action.completed_at = receipt.finished_at
        self._conn.execute(
            "INSERT INTO episodes (episode_id,event_id,subject_id,record_json,created_at) VALUES (?,?,?,?,?)",
            (
                record.episode_id,
                record.event_id,
                record.subject_id,
                self._encode(record),
                time.time(),
            ),
        )
        task_id = record.correlation_id
        if task_id:
            terminal = record.metadata["terminal_state"]
            safe = {
                "task_id": task_id,
                "event_id": record.event_id,
                "terminal_state": terminal,
                "ok": terminal == "succeeded",
            }
            self._conn.execute(
                "INSERT INTO episode_receipt_outbox (event_id,task_id,schema_version,receipt_json) VALUES (?,?,1,?)",
                (record.event_id, task_id, json.dumps(safe, ensure_ascii=True)),
            )
        self._conn.execute(
            "DELETE FROM episode_processing WHERE event_id=?", (record.event_id,)
        )

    def record_receipt(self, receipt):
        event_id = receipt.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return
        with self._transaction():
            record = self._draft(event_id)
            if record is not None and record.metadata.get("task_id") != receipt.get(
                "task_id"
            ):
                return
            if record is not None:
                self._apply_receipt(record, receipt)
                self._seal(record)
        # Never hold the SQLite transaction/lock while committing to another store.
        if self._receipt_sink is not None:
            self.deliver_receipts(self._receipt_sink)

    @staticmethod
    def _apply_receipt(record, receipt):
        terminal = receipt.get("terminal_state")
        if not isinstance(terminal, str) or terminal not in {
            "succeeded",
            "failed",
            "rejected",
            "expired",
            "outcome_unknown",
        }:
            raise ValueError("invalid processing terminal state")
        if "ok" in receipt and (
            type(receipt["ok"]) is not bool
            or receipt["ok"] != (terminal == "succeeded")
        ):
            raise ValueError("inconsistent processing terminal outcome")
        status: Literal["succeeded", "failed", "unknown"] = (
            "succeeded"
            if terminal == "succeeded"
            else "unknown"
            if terminal == "outcome_unknown"
            else "failed"
        )
        record.result = EpisodeResult(
            status=status,
            ok=True if status == "succeeded" else False if status == "failed" else None,
        )
        record.completed_at = datetime.now(timezone.utc)
        record.metadata["terminal_state"] = terminal
        record.metadata["completion_kind"] = "canonical_receipt"
        for action in record.actions:
            if action.status == "started":
                action.status = "unknown"

    def _outbox_receipt(self, row):
        encoded = row["receipt_json"]
        if (
            row["schema_version"] != 1
            or not isinstance(encoded, str)
            or len(encoded.encode("utf-8")) > 8192
        ):
            raise ValueError("invalid processing receipt envelope")
        receipt = json.loads(encoded)
        if not isinstance(receipt, dict) or set(receipt) != {
            "task_id",
            "event_id",
            "terminal_state",
            "ok",
        }:
            raise ValueError("invalid processing receipt fields")
        terminal = receipt["terminal_state"]
        if (
            receipt["task_id"] != row["task_id"]
            or receipt["event_id"] != row["event_id"]
            or not isinstance(terminal, str)
            or terminal
            not in {"succeeded", "failed", "rejected", "expired", "outcome_unknown"}
            or type(receipt["ok"]) is not bool
            or receipt["ok"] != (terminal == "succeeded")
        ):
            raise ValueError("inconsistent processing receipt")
        episode = self.get_by_event(row["event_id"])
        if (
            episode is None
            or episode.event_id != row["event_id"]
            or episode.schema_version != 2
            or episode.correlation_id != row["task_id"]
            or episode.metadata.get("terminal_state") != terminal
            or episode.completed_at is None
            or episode.result.status
            != (
                "succeeded"
                if terminal == "succeeded"
                else "unknown"
                if terminal == "outcome_unknown"
                else "failed"
            )
        ):
            raise ValueError("processing receipt does not match sealed Episode")
        return receipt

    def receipt_for_task(self, task_id):
        """Internal compact receipt; no model reply or original request payload."""
        with self._lock:
            if self._closed:
                raise RuntimeError("processing Episode store is closed")
            row = self._conn.execute(
                "SELECT * FROM episode_receipt_outbox WHERE task_id=?", (task_id,)
            ).fetchone()
            return self._outbox_receipt(row) if row is not None else None

    def attach_receipt_sink(self, sink):
        """Attach before consumers start; replay notification only, never actions."""
        self._receipt_sink = sink
        while self.deliver_receipts(sink):
            pass

    def deliver_receipts(self, sink, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("receipt delivery limit must be between 1 and 128")
        # Another delivery already covers the current queue. Avoid waiting on a
        # second store while holding the registry's canonical-receipt lock.
        if not self._delivery_lock.acquire(blocking=False):
            return 0
        try:
            with self._lock:
                if self._closed:
                    raise RuntimeError("processing Episode store is closed")
                rows = self._conn.execute(
                    "SELECT * FROM episode_receipt_outbox WHERE delivered=0 ORDER BY event_id LIMIT ?",
                    (limit,),
                ).fetchall()
                receipts = [self._outbox_receipt(row) for row in rows]
            delivered = 0
            for receipt in receipts:
                try:
                    sink(receipt)
                    # A crash before this ack causes a safe, idempotent redelivery.
                    with self._transaction():
                        self._conn.execute(
                            "UPDATE episode_receipt_outbox SET delivered=1 WHERE event_id=?",
                            (receipt["event_id"],),
                        )
                except Exception:
                    self._delivery_errors += 1
                    raise
                delivered += 1
            return delivered
        finally:
            self._delivery_lock.release()

    def recover(self, *, receipt_lookup=None):
        """Seal interrupted drafts using known receipts or unknown; never replay."""
        with self._transaction():
            rows = self._conn.execute(
                "SELECT event_id FROM episode_processing"
            ).fetchall()
            for row in rows:
                record = self._draft(row[0])
                known = (
                    receipt_lookup(record.correlation_id, record.event_id)
                    if receipt_lookup is not None and record.correlation_id
                    else None
                )
                if known is not None:
                    if (
                        known.get("task_id") != record.correlation_id
                        or known.get("event_id") != record.event_id
                        or type(known.get("ok")) is not bool
                    ):
                        raise ValueError("recovery receipt identity mismatch")
                    self._apply_receipt(record, known)
                    record.metadata["receipt_recovered"] = True
                else:
                    record.result = EpisodeResult(status="unknown", ok=None)
                    record.completed_at = datetime.now(timezone.utc)
                    record.metadata["completion_kind"] = "recovery_unknown"
                    record.metadata["terminal_state"] = "outcome_unknown"
                    for action in record.actions:
                        if action.status == "started":
                            action.status = "unknown"
                self._seal(record)
            return len(rows)

    def stats(self):
        with self._lock:
            return {
                **super().stats(),
                "processing": self._conn.execute(
                    "SELECT COUNT(*) FROM episode_processing"
                ).fetchone()[0],
                "receipt_notifications_pending": self._conn.execute(
                    "SELECT COUNT(*) FROM episode_receipt_outbox WHERE delivered=0"
                ).fetchone()[0],
                "receipt_delivery_errors": self._delivery_errors,
            }

    def close(self):
        with self._lock:
            if not self._closed:
                super().close()
                self._closed = True
