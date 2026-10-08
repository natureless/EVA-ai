"""Atomic SQLite state revisions, event history and planned action outbox.

Claimed actions are never lease-replayed. Startup recovery explicitly seals them
unknown; a new runtime may dispatch only intents that were never claimed.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from packages.contracts.actions import (
    ActionIntent,
    ActionReceipt,
    finite_json,
    read_action_record,
)
from packages.contracts.events import EventEnvelope
from packages.contracts.state import ConsciousState, StateDelta
from packages.kernel.state_repository import InMemoryStateRepository

MAX_STATE_BYTES = 262_144
MAX_COMMIT_BYTES = 1_048_576


def checked_state(state: ConsciousState) -> ConsciousState:
    if type(state.version) is not int or type(state.tick) is not int:
        raise ValueError("invalid state revision or tick type")
    raw = state.model_dump(mode="json")
    if (
        type(raw["version"]) is not int
        or raw["version"] < 0
        or type(raw["tick"]) is not int
        or raw["tick"] < 0
    ):
        raise ValueError("invalid state revision or tick")
    if not isinstance(raw["subject_id"], str) or not 1 <= len(raw["subject_id"]) <= 256:
        raise ValueError("invalid state subject")
    copied = ConsciousState.model_validate_json(finite_json(raw, MAX_STATE_BYTES))
    if copied.integrity_hash != copied.compute_integrity_hash():
        raise ValueError("state integrity_hash mismatch")
    return copied


def read_state(encoded: str) -> ConsciousState:
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("invalid stored state size")
    raw = json.loads(encoded)
    if not isinstance(raw, dict) or set(raw) != set(ConsciousState.model_fields):
        raise ValueError("invalid stored state shape")
    for key in ("version", "tick"):
        if type(raw[key]) is not int or raw[key] < 0:
            raise ValueError("invalid stored state revision")
    return checked_state(ConsciousState.model_validate_json(encoded))


class SQLiteStateRepository:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS cognitive_states (
        subject_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
        version INTEGER NOT NULL CHECK(version>=0), state_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cognitive_state_commits (
        subject_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1),
        schema_version INTEGER NOT NULL, state_json TEXT NOT NULL, events_json TEXT NOT NULL,
        PRIMARY KEY(subject_id,version)
    );
    CREATE TABLE IF NOT EXISTS cognitive_action_outbox (
        action_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, state_version INTEGER NOT NULL,
        source_event_id TEXT NOT NULL, slot TEXT NOT NULL, intent_json TEXT NOT NULL,
        receipt_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','executing','completed','unknown')),
        UNIQUE(subject_id,source_event_id,slot),
        FOREIGN KEY(subject_id,state_version) REFERENCES cognitive_state_commits(subject_id,version)
    );
    CREATE INDEX IF NOT EXISTS idx_cognitive_actions_pending ON cognitive_action_outbox(status,subject_id,state_version,action_id);
    """

    def __init__(
        self, db_path: str | Path, *, initial_state: ConsciousState | None = None
    ):
        self._lock = threading.RLock()
        self._closed = False
        self._active_dispatches = 0
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(self.SCHEMA)
            if initial_state is not None:
                seed = initial_state.model_copy(deep=True)
                if seed.version != 0:
                    raise ValueError("initial state must have revision zero")
                if not seed.integrity_hash:
                    seed.integrity_hash = seed.compute_integrity_hash()
                seed = checked_state(seed)
                self._conn.execute(
                    "INSERT OR IGNORE INTO cognitive_states VALUES (?,1,0,?)",
                    (seed.subject_id, seed.model_dump_json()),
                )
                self._conn.commit()
        except BaseException:
            self._conn.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._check_open()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _check_open(self):
        if self._closed:
            raise RuntimeError("state/action repository is closed")

    def _load(self, subject_id):
        if not isinstance(subject_id, str) or not 1 <= len(subject_id) <= 256:
            raise ValueError("invalid state subject")
        row = self._conn.execute(
            "SELECT schema_version,version,CASE WHEN length(CAST(state_json AS BLOB))<=? THEN state_json END AS state_json FROM cognitive_states WHERE subject_id=?",
            (MAX_STATE_BYTES, subject_id),
        ).fetchone()
        if row is None:
            state = ConsciousState(subject_id=subject_id)
            state.integrity_hash = state.compute_integrity_hash()
            return state
        state = read_state(row["state_json"])
        if (
            row["schema_version"] != 1
            or state.subject_id != subject_id
            or state.version != row["version"]
        ):
            raise ValueError("stored state identity or version mismatch")
        return state

    def load_sync(self, subject_id):
        with self._lock:
            self._check_open()
            return self._load(subject_id)

    async def load(self, subject_id: str) -> ConsciousState:
        return await asyncio.to_thread(self.load_sync, subject_id)

    @staticmethod
    def _events(events, subject_id):
        if len(events) > 64:
            raise ValueError("state commit event limit exceeded")
        result = [
            EventEnvelope.model_validate_json(finite_json(e.model_dump(mode="json")))
            for e in events
        ]
        if any(e.subject_id != subject_id for e in result) or len(
            {e.event_id for e in result}
        ) != len(result):
            raise ValueError(
                "state commit events must have distinct IDs and matching subjects"
            )
        finite_json([e.model_dump(mode="json") for e in result], MAX_COMMIT_BYTES)
        return result

    def _commit(self, previous_version, new_state, events, actions):
        current = self._load(new_state.subject_id)
        InMemoryStateRepository._check_expected_version(
            current, previous_version, new_state.version
        )
        if any(a.source_event_id not in {e.event_id for e in events} for a in actions):
            raise ValueError("action must bind a source event in its state commit")
        self._conn.execute(
            "INSERT INTO cognitive_state_commits VALUES (?,?,1,?,?)",
            (
                new_state.subject_id,
                new_state.version,
                new_state.model_dump_json(),
                finite_json(
                    [e.model_dump(mode="json") for e in events], MAX_COMMIT_BYTES
                ),
            ),
        )
        self._conn.execute(
            "INSERT INTO cognitive_states VALUES (?,1,?,?) ON CONFLICT(subject_id) DO UPDATE SET schema_version=1,version=excluded.version,state_json=excluded.state_json",
            (new_state.subject_id, new_state.version, new_state.model_dump_json()),
        )
        for intent in actions:
            receipt = ActionReceipt(
                action_id=intent.action_id,
                subject_id=new_state.subject_id,
                source_event_id=intent.source_event_id,
                state_version=new_state.version,
                request_hash=intent.request_hash,
            )
            self._conn.execute(
                "INSERT INTO cognitive_action_outbox VALUES (?,?,?,?,?,?,?,?)",
                (
                    intent.action_id,
                    new_state.subject_id,
                    new_state.version,
                    intent.source_event_id,
                    intent.slot,
                    intent.model_dump_json(),
                    receipt.model_dump_json(),
                    receipt.status,
                ),
            )
        return new_state.version

    def commit_sync(self, previous_version, new_state, events, actions=()):
        if type(previous_version) is not int or previous_version < 0:
            raise ValueError("invalid expected state revision")
        state = checked_state(new_state)
        copied_events = self._events(events, state.subject_id)
        if len(actions) > 16:
            raise ValueError("state commit action limit exceeded")
        if any(
            type(a.schema_version) is not int or a.schema_version != 1 for a in actions
        ):
            raise ValueError("invalid action intent schema version")
        intents = [
            read_action_record(a.model_dump_json(), ActionIntent) for a in actions
        ]
        with self._transaction():
            return self._commit(previous_version, state, copied_events, intents)

    async def commit(
        self,
        previous_version: int,
        new_state: ConsciousState,
        events: list[EventEnvelope],
    ) -> int:
        return await self.commit_with_actions(previous_version, new_state, events, [])

    async def commit_with_actions(
        self,
        previous_version: int,
        new_state: ConsciousState,
        events: list[EventEnvelope],
        actions: list[ActionIntent],
    ) -> int:
        # Freeze caller models before yielding to another thread.
        return await asyncio.to_thread(
            self.commit_sync,
            previous_version,
            new_state.model_copy(deep=True),
            [e.model_copy(deep=True) for e in events],
            [a.model_copy(deep=True) for a in actions],
        )

    def _commit_delta(self, delta, events, subject_id):
        if type(delta.base_version) is not int or delta.base_version < 0:
            raise ValueError("invalid delta base revision")
        copied_events = self._events(events, subject_id)
        with self._transaction():
            return self._commit(
                delta.base_version,
                checked_state(delta.apply(self._load(subject_id))),
                copied_events,
                [],
            )

    async def commit_delta(
        self,
        delta: StateDelta,
        events: list[EventEnvelope] | None = None,
        subject_id: str = "eva-001",
    ) -> int:
        return await asyncio.to_thread(
            self._commit_delta,
            delta.model_copy(deep=True),
            [e.model_copy(deep=True) for e in events or []],
            subject_id,
        )

    def _history(self, subject_id, from_version, to_version, limit):
        if (
            any(
                type(v) is not int or v < 0
                for v in (
                    from_version,
                    to_version if to_version is not None else from_version,
                )
            )
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid state history bounds")
        with self._lock:
            self._check_open()
            self._load(subject_id)
            rows = self._conn.execute(
                "SELECT version,schema_version,CASE WHEN length(CAST(state_json AS BLOB))<=? THEN state_json END AS state_json,CASE WHEN length(CAST(events_json AS BLOB))<=? THEN events_json END AS events_json FROM cognitive_state_commits WHERE subject_id=? AND version>? AND (? IS NULL OR version<=?) ORDER BY version LIMIT ?",
                (
                    MAX_STATE_BYTES,
                    MAX_COMMIT_BYTES,
                    subject_id,
                    from_version,
                    to_version,
                    to_version,
                    limit,
                ),
            ).fetchall()
            result = []
            for row in rows:
                state = read_state(row["state_json"])
                raw_events = json.loads(row["events_json"])
                if (
                    row["schema_version"] != 1
                    or state.subject_id != subject_id
                    or state.version != row["version"]
                    or not isinstance(raw_events, list)
                ):
                    raise ValueError("invalid state history identity")
                events = self._events(
                    [EventEnvelope.model_validate(e) for e in raw_events], subject_id
                )
                result.append({"state": state, "events": events})
            return result

    async def history(
        self,
        subject_id: str,
        *,
        from_version: int = 0,
        to_version: int | None = None,
        limit: int = 20,
    ):
        return await asyncio.to_thread(
            self._history, subject_id, from_version, to_version, limit
        )

    async def replay(
        self, subject_id: str, from_version: int = 0, to_version: int | None = None
    ) -> list[EventEnvelope]:
        history = await self.history(
            subject_id, from_version=from_version, to_version=to_version, limit=100
        )
        if len(history) == 100:
            following = await self.history(
                subject_id,
                from_version=history[-1]["state"].version,
                to_version=to_version,
                limit=1,
            )
            if following:
                raise ValueError("replay range exceeds limit; use paged state history")
        return [event for commit in history for event in commit["events"]]

    def _action(self, action_id):
        row = self._conn.execute(
            "SELECT action_id,subject_id,state_version,source_event_id,slot,status,CASE WHEN length(CAST(intent_json AS BLOB))<=65536 THEN intent_json END AS safe_intent,CASE WHEN length(CAST(receipt_json AS BLOB))<=65536 THEN receipt_json END AS safe_receipt FROM cognitive_action_outbox WHERE action_id=?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise KeyError(action_id)
        intent = read_action_record(row["safe_intent"], ActionIntent)
        receipt = read_action_record(row["safe_receipt"], ActionReceipt)
        if (
            intent.action_id != action_id
            or receipt.action_id != action_id
            or receipt.subject_id != row["subject_id"]
            or receipt.state_version != row["state_version"]
            or receipt.status != row["status"]
            or intent.source_event_id != row["source_event_id"]
            or receipt.source_event_id != row["source_event_id"]
            or intent.slot != row["slot"]
            or receipt.request_hash != intent.request_hash
        ):
            raise ValueError("action outbox identity mismatch")
        history = self._history(
            receipt.subject_id, receipt.state_version - 1, receipt.state_version, 1
        )
        if not history or intent.source_event_id not in {
            e.event_id for e in history[0]["events"]
        }:
            raise ValueError("action state/event binding unavailable")
        return intent, receipt, history[0]["state"]

    def action(self, action_id):
        with self._lock:
            self._check_open()
            return self._action(action_id)

    def action_for_source(self, subject_id, source_event_id, slot):
        with self._lock:
            self._check_open()
            row = self._conn.execute(
                "SELECT action_id FROM cognitive_action_outbox WHERE subject_id=? AND source_event_id=? AND slot=?",
                (subject_id, source_event_id, slot),
            ).fetchone()
            return self._action(row["action_id"]) if row else None

    def source_event_for_action(self, action_id):
        with self._lock:
            self._check_open()
            _, receipt, _ = self._action(action_id)
            history = self._history(
                receipt.subject_id, receipt.state_version - 1, receipt.state_version, 1
            )
            return next(
                e for e in history[0]["events"] if e.event_id == receipt.source_event_id
            )

    def pending_actions(self, *, subject_id=None, limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("invalid action page limit")
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                "SELECT action_id FROM cognitive_action_outbox WHERE status='pending' AND (? IS NULL OR subject_id=?) ORDER BY state_version,action_id LIMIT ?",
                (subject_id, subject_id, limit),
            ).fetchall()
            return [self._action(r["action_id"])[0] for r in rows]

    def _save_receipt(self, receipt):
        read_action_record(receipt.model_dump_json(), ActionReceipt)
        self._conn.execute(
            "UPDATE cognitive_action_outbox SET receipt_json=?,status=? WHERE action_id=?",
            (receipt.model_dump_json(), receipt.status, receipt.action_id),
        )

    def claim_action(self, action_id):
        with self._transaction():
            intent, receipt, state = self._action(action_id)
            if receipt.status != "pending":
                return None
            claimed = ActionReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": "executing",
                    "claim_token": f"claim_{uuid4().hex}",
                    "started_at": datetime.now(timezone.utc),
                }
            )
            self._save_receipt(claimed)
            return intent, claimed, state

    def finish_action(
        self, action_id, claim_token, *, returned_ok, observation_kind="handler_return"
    ):
        with self._transaction():
            _, receipt, _ = self._action(action_id)
            if receipt.claim_token != claim_token:
                raise ValueError("action claim token mismatch")
            if receipt.status != "executing":
                return False
            finished = ActionReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": "completed" if returned_ok is True else "unknown",
                    "observation_kind": observation_kind,
                    "returned_ok": returned_ok,
                    "finished_at": datetime.now(timezone.utc),
                }
            )
            self._save_receipt(finished)
            return True

    def recover_actions(self, *, limit=256):
        """Startup-only, after previous workers have exited. Never redispatch claims."""
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("invalid action recovery batch limit")
        with self._transaction():
            if self._active_dispatches:
                raise RuntimeError("cannot recover while action dispatcher is active")
            rows = self._conn.execute(
                "SELECT action_id FROM cognitive_action_outbox WHERE status='executing' ORDER BY action_id LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                _, receipt, _ = self._action(row["action_id"])
                recovered = ActionReceipt.model_validate(
                    {
                        **receipt.model_dump(),
                        "status": "unknown",
                        "observation_kind": "recovery_unknown",
                        "sealed_at": datetime.now(timezone.utc),
                    }
                )
                self._save_receipt(recovered)
            return len(rows)

    @contextmanager
    def dispatch_scope(self):
        with self._lock:
            self._check_open()
            self._active_dispatches += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_dispatches -= 1

    def close(self):
        with self._lock:
            if self._closed:
                return
            if self._active_dispatches:
                raise RuntimeError("cannot close while action dispatcher is active")
            self._conn.close()
            self._closed = True
