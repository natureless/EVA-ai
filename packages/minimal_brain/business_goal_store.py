"""Transactional goal state and canonical receipt projection in the EVA database.

Only the application adapter may supply receipts. A successful request is not a
successful business goal. No text, model claims or receipt metadata is evaluated
as a business success predicate here.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4

from packages.minimal_brain.goal_graph import project_goals
from packages.minimal_brain.goal_episodes import read_goal_episode

from packages.minimal_brain.business_goals import (
    BusinessGoal,
    BusinessGoalConflict,
    BusinessGoalLedger,
    BusinessGoalStatus,
)


class BusinessGoalStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS business_goals (
        goal_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL UNIQUE,
        source_event_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        record_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_business_goals_event ON business_goals(source_event_id);
    CREATE TABLE IF NOT EXISTS business_goal_receipts (
        goal_id TEXT PRIMARY KEY REFERENCES business_goals(goal_id),
        receipt_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS business_goal_verification_specs (
        goal_id TEXT PRIMARY KEY REFERENCES business_goals(goal_id),
        spec_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS business_goal_verifications (
        run_id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL REFERENCES business_goals(goal_id),
        goal_version INTEGER NOT NULL,
        record_json TEXT NOT NULL,
        UNIQUE(goal_id, goal_version)
    );
    """

    def __init__(self, db_path: str | Path, *, clock=None, verifier=None):
        self._lock = RLock()
        self._clock = clock
        self._closed = False
        self.verifier = verifier
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=2)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(self.SCHEMA)
        except BaseException:
            self._conn.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("business goal store is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    @staticmethod
    def _encode(value):
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )

    def _ledger(self, row, *, recover=False):
        if row["schema_version"] != 1:
            raise ValueError("unsupported business goal schema")
        ledger = BusinessGoalLedger(clock=self._clock)
        raw = json.loads(row["record_json"])
        if (
            raw.get("goal_id") != row["goal_id"]
            or raw.get("version") != row["version"]
            or raw.get("status") != row["status"]
            or raw.get("source_event_id") != row["source_event_id"]
        ):
            raise ValueError("inconsistent stored business goal")
        ledger.restore([raw], recover=recover)
        return ledger

    def _save(self, goal, old_version):
        updated = self._conn.execute(
            "UPDATE business_goals SET version=?,status=?,record_json=? WHERE goal_id=? AND version=?",
            (
                goal.version,
                goal.status.value,
                self._encode(goal.snapshot()),
                goal.goal_id,
                old_version,
            ),
        )
        if updated.rowcount != 1:
            raise BusinessGoalConflict("goal version changed")

    def create(
        self,
        *,
        task_id: str,
        source_event_id: str,
        description: str,
        success_conditions: list[dict],
        deadline=None,
        verification: dict | None = None,
    ) -> dict:
        if not task_id or not source_event_id:
            raise ValueError("goal requires registered task and event IDs")
        goal = BusinessGoal.model_validate(
            {
                "description": description,
                "source_event_id": source_event_id,
                "success_conditions": success_conditions,
                "deadline": deadline,
            }
        )
        if not goal.success_conditions or any(
            not c.field.startswith("checks.") for c in goal.success_conditions
        ):
            raise ValueError("business success conditions must reference checks.*")
        spec = None
        if verification is not None:
            if self.verifier is None:
                raise ValueError("business verifier is not attached")
            spec = self.verifier.validate_spec(verification)
            if (
                len(goal.success_conditions) != 1
                or goal.success_conditions[0].field != "checks.files_match"
                or goal.success_conditions[0].operator != "equals"
                or goal.success_conditions[0].expected is not True
            ):
                raise ValueError(
                    "file verification requires checks.files_match equals true"
                )
        ledger = BusinessGoalLedger(clock=self._clock)
        ledger.create(goal)
        active = ledger.transition(
            goal.goal_id, "active", expected_version=goal.version
        )
        with self._transaction():
            if self._conn.execute(
                "SELECT 1 FROM business_goals WHERE task_id=?", (task_id,)
            ).fetchone():
                raise BusinessGoalConflict("task already has a business goal")
            self._conn.execute(
                "INSERT INTO business_goals (goal_id,task_id,source_event_id,schema_version,version,status,record_json) VALUES (?,?,?,?,?,?,?)",
                (
                    active.goal_id,
                    task_id,
                    source_event_id,
                    1,
                    active.version,
                    active.status.value,
                    self._encode(active.snapshot()),
                ),
            )
            if spec is not None:
                self._conn.execute(
                    "INSERT INTO business_goal_verification_specs VALUES (?,?)",
                    (goal.goal_id, self._encode(spec)),
                )
        return self.get(active.goal_id)

    def _row(self, goal_id):
        row = self._conn.execute(
            "SELECT * FROM business_goals WHERE goal_id=?", (goal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return row

    def get(self, goal_id: str) -> dict:
        # Expiration is an explicit transactional update, not an in-memory-only view.
        with self._transaction():
            row = self._row(goal_id)
            ledger = self._ledger(row)
            expired = ledger.expire_due()
            if expired:
                self._save(expired[0], row["version"])
            record = ledger.get(goal_id).snapshot()
            receipt = self._conn.execute(
                "SELECT receipt_json FROM business_goal_receipts WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            spec = self._conn.execute(
                "SELECT spec_json FROM business_goal_verification_specs WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            latest = self._conn.execute(
                "SELECT record_json FROM business_goal_verifications WHERE goal_id=? ORDER BY goal_version DESC LIMIT 1",
                (goal_id,),
            ).fetchone()
            verification = json.loads(latest[0]) if latest else None
            return {
                **record,
                "task_id": row["task_id"],
                "schema_version": 1,
                "processing_receipt": json.loads(receipt[0]) if receipt else None,
                "verification_state": verification["outcome"]
                if verification
                else "not_verified",
                "verification": json.loads(spec[0]) if spec else None,
                "last_verification": verification,
            }

    def graph(self, *, limit=40, offset=0, query=""):
        """One SQLite read snapshot; never reconcile, expire, recover or verify."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 40
            or type(offset) is not int
            or not 0 <= offset <= 2_147_483_647
            or not isinstance(query, str)
            or len(query) > 200
        ):
            raise ValueError("invalid goal projection bounds")
        with self._lock:
            if self._closed:
                raise RuntimeError("business goal store is closed")
            self._conn.execute("BEGIN")
            try:
                where = """WHERE instr(lower(json_extract(record_json, '$.description')), lower(?)) > 0
                    OR instr(goal_id, ?) > 0 OR instr(task_id, ?) > 0 OR instr(source_event_id, ?) > 0"""
                args = (query,) * 4
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM business_goals " + where, args
                ).fetchone()[0]
                rows = self._conn.execute(
                    "SELECT * FROM business_goals "
                    + where
                    + " ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (*args, limit, offset),
                ).fetchall()
                records = []
                for row in rows:
                    goal = self._ledger(row).get(row["goal_id"]).snapshot()
                    goal["task_id"] = row["task_id"]
                    receipt = self._conn.execute(
                        "SELECT receipt_json FROM business_goal_receipts WHERE goal_id=?",
                        (row["goal_id"],),
                    ).fetchone()
                    spec = self._conn.execute(
                        "SELECT spec_json FROM business_goal_verification_specs WHERE goal_id=?",
                        (row["goal_id"],),
                    ).fetchone()
                    goal["verification"] = json.loads(spec[0]) if spec else None
                    episode_reference = read_goal_episode(
                        self._conn, row["source_event_id"]
                    )
                    goal["episode_reference"] = {
                        "status": episode_reference["status"],
                        "reason": episode_reference["reason"],
                        "episode_id": episode_reference["episode"]["episode_id"]
                        if episode_reference["episode"]
                        else None,
                    }
                    count = self._conn.execute(
                        "SELECT COUNT(*) FROM business_goal_verifications WHERE goal_id=?",
                        (row["goal_id"],),
                    ).fetchone()[0]
                    runs = self._conn.execute(
                        "SELECT record_json FROM business_goal_verifications WHERE goal_id=? ORDER BY goal_version DESC LIMIT 5",
                        (row["goal_id"],),
                    ).fetchall()
                    records.append(
                        {
                            "goal": goal,
                            "receipt": json.loads(receipt[0]) if receipt else None,
                            "runs": [json.loads(run[0]) for run in runs],
                            "verification_count": count,
                            "episode_reference": episode_reference,
                        }
                    )
                result = project_goals(
                    records, total=total, offset=offset, limit=limit, query=query
                )
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise

    def episode_reference(self, goal_id: str):
        """Inspect an existing source-event episode without reconciling the goal."""
        with self._lock:
            if self._closed:
                raise RuntimeError("business goal store is closed")
            self._conn.execute("BEGIN")
            try:
                row = self._row(goal_id)
                # Refuse inconsistent goal identities before deriving the reference.
                self._ledger(row)
                result = {
                    "schema_version": 1,
                    "goal_id": row["goal_id"],
                    "source_event_id": row["source_event_id"],
                    "current_goal_version": row["version"],
                    "current_status": row["status"],
                    **read_goal_episode(self._conn, row["source_event_id"]),
                    "read_only": True,
                }
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise

    def verification_history(self, goal_id: str, *, limit=20, offset=0):
        """Read historical verification samples without reconciling or expiring the goal."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or type(offset) is not int
            or not 0 <= offset <= 2_147_483_647
        ):
            raise ValueError("invalid verification history bounds")
        with self._lock:
            if self._closed:
                raise RuntimeError("business goal store is closed")
            self._conn.execute("BEGIN")
            try:
                row = self._row(goal_id)
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM business_goal_verifications WHERE goal_id=?",
                    (goal_id,),
                ).fetchone()[0]
                records = self._conn.execute(
                    "SELECT record_json FROM business_goal_verifications "
                    "WHERE goal_id=? ORDER BY goal_version DESC LIMIT ? OFFSET ?",
                    (goal_id, limit, offset),
                ).fetchall()
                result = {
                    "schema_version": 1,
                    "goal_id": goal_id,
                    "current_goal_version": row["version"],
                    "current_status": row["status"],
                    "records": [json.loads(item[0]) for item in records],
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "next_offset": offset + limit
                    if offset + len(records) < total
                    else None,
                    "read_only": True,
                }
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise

    def _check_verifiable(self, goal, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        if goal.version != expected_version:
            raise BusinessGoalConflict("goal version changed")
        if goal.status not in {
            BusinessGoalStatus.PENDING_VERIFICATION,
            BusinessGoalStatus.INTERRUPTED,
        }:
            raise BusinessGoalConflict("goal is not awaiting business verification")
        receipt = self._conn.execute(
            "SELECT receipt_json FROM business_goal_receipts WHERE goal_id=?",
            (goal.goal_id,),
        ).fetchone()
        if receipt is None or json.loads(receipt[0])["terminal_state"] != "succeeded":
            raise BusinessGoalConflict("successful processing receipt is required")

    def verify(self, goal_id: str, *, expected_version: int) -> dict:
        """Run the injected checker outside DB locks, then commit with a version fence."""
        if self.verifier is None:
            raise ValueError("business verifier is not attached")
        with self._transaction():
            row = self._row(goal_id)
            ledger = self._ledger(row)
            goal = ledger.get(goal_id)
            self._check_verifiable(goal, expected_version)
            ledger._check_deadline(goal, ledger._now())
            saved = self._conn.execute(
                "SELECT spec_json FROM business_goal_verification_specs WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if saved is None:
                raise ValueError("goal has no immutable verification specification")
            encoded_spec = saved[0]
            source_event_id = goal.source_event_id
        observation = self.verifier.run(json.loads(encoded_spec))
        run_id = f"verification_{uuid4().hex}"
        with self._transaction():
            row = self._row(goal_id)
            ledger = self._ledger(row)
            goal = ledger.get(goal_id)
            self._check_verifiable(goal, expected_version)
            current_spec = self._conn.execute(
                "SELECT spec_json FROM business_goal_verification_specs WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if (
                current_spec is None
                or current_spec[0] != encoded_spec
                or goal.source_event_id != source_event_id
            ):
                raise BusinessGoalConflict("verification binding changed")
            if goal.status is BusinessGoalStatus.INTERRUPTED:
                goal = ledger.transition(
                    goal_id, "active", expected_version=goal.version
                )
            updated = ledger.observe(
                goal_id,
                {"checks": observation["checks"]},
                expected_version=goal.version,
                evidence={
                    "kind": "file_verification",
                    "run_id": run_id,
                    "verifier_id": observation["verifier_id"],
                },
            )
            result = {
                **observation,
                "schema_version": 1,
                "run_id": run_id,
                "goal_id": goal_id,
                "source_event_id": source_event_id,
                "base_goal_version": expected_version,
                "committed_goal_version": updated.version,
            }
            self._save(updated, row["version"])
            self._conn.execute(
                "INSERT INTO business_goal_verifications VALUES (?,?,?,?)",
                (run_id, goal_id, updated.version, self._encode(result)),
            )
        return self.get(goal_id)

    def cancel(self, goal_id: str, *, expected_version: int) -> dict:
        with self._transaction():
            row = self._row(goal_id)
            ledger = self._ledger(row)
            goal = ledger.transition(
                goal_id, "cancelled", expected_version=expected_version
            )
            self._save(goal, row["version"])
        return self.get(goal_id)

    def get_by_task(self, task_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT goal_id FROM business_goals WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            return self.get(row[0])

    def record_receipt(self, receipt: dict[str, Any]) -> None:
        """Internal sink for the canonical registry; never accepts client observations."""
        task_id, event_id = receipt.get("task_id"), receipt.get("event_id")
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM business_goals WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return
            if event_id != row["source_event_id"]:
                raise BusinessGoalConflict("receipt event does not match goal")
            terminal, ok = receipt.get("terminal_state"), receipt.get("ok")
            if terminal not in {
                "succeeded",
                "failed",
                "rejected",
                "expired",
                "outcome_unknown",
            }:
                raise ValueError("invalid receipt terminal state")
            if type(ok) is not bool or ok != (terminal == "succeeded"):
                raise ValueError("inconsistent receipt outcome")
            # Persist only server-owned execution fields, never reply/review/model metadata.
            safe = {
                "task_id": task_id,
                "event_id": event_id,
                "terminal_state": terminal,
                "ok": ok,
            }
            encoded = self._encode(safe)
            previous = self._conn.execute(
                "SELECT receipt_json FROM business_goal_receipts WHERE goal_id=?",
                (row["goal_id"],),
            ).fetchone()
            if previous:
                if previous[0] != encoded:
                    raise BusinessGoalConflict("canonical receipt already recorded")
                return
            ledger = self._ledger(row)
            expired = ledger.expire_due()
            goal = ledger.get(row["goal_id"])
            if not expired and goal.status is BusinessGoalStatus.ACTIVE:
                target = {
                    "succeeded": "pending_verification",
                    "outcome_unknown": "interrupted",
                    "expired": "expired",
                    "rejected": "failed",
                    "failed": "failed",
                }[terminal]
                goal = ledger.transition(
                    goal.goal_id,
                    target,
                    expected_version=goal.version,
                    evidence={"kind": "processing_receipt", **safe},
                )
            if goal.version != row["version"]:
                self._save(goal, row["version"])
            self._conn.execute(
                "INSERT INTO business_goal_receipts VALUES (?,?)",
                (goal.goal_id, encoded),
            )

    def processing_receipt_for_task(self, task_id, event_id):
        """Startup-only read: no reconciliation, expiry or write transaction."""
        with self._lock:
            if self._closed:
                raise RuntimeError("business goal store is closed")
            row = self._conn.execute(
                "SELECT r.receipt_json,g.source_event_id FROM business_goals g JOIN business_goal_receipts r ON r.goal_id=g.goal_id WHERE g.task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            encoded = row["receipt_json"]
            if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 8192:
                raise ValueError("invalid stored processing receipt")
            receipt = json.loads(encoded)
            if (
                not isinstance(receipt, dict)
                or set(receipt) != {"task_id", "event_id", "terminal_state", "ok"}
                or row["source_event_id"] != event_id
                or receipt.get("event_id") != event_id
                or receipt.get("task_id") != task_id
            ):
                raise ValueError("stored processing receipt identity mismatch")
            return receipt

    def recover_receipts(self, receipt_lookup) -> int:
        """Startup-only reconcile durable summaries before goal recovery.

        Receipts may predate goal registration, so a delivered notification
        alone cannot prove that a subsequently inserted goal received it.
        Read other stores outside this store's transaction and lock.
        """
        after = ""
        recovered = 0
        while True:
            with self._lock:
                if self._closed:
                    raise RuntimeError("business goal store is closed")
                rows = self._conn.execute(
                    "SELECT g.task_id,g.source_event_id FROM business_goals g LEFT JOIN business_goal_receipts r ON r.goal_id=g.goal_id WHERE r.goal_id IS NULL AND g.task_id>? ORDER BY g.task_id LIMIT 256",
                    (after,),
                ).fetchall()
            if not rows:
                return recovered
            for row in rows:
                known = receipt_lookup(row["task_id"], row["source_event_id"])
                if known is not None:
                    self.record_receipt(known)
                    recovered += 1
            after = rows[-1]["task_id"]

    def recover(self, *, unclaimed_lookup=None) -> int:
        """Run once before starting the owner runtime. Recovery never executes work."""
        changed = 0
        preserved = set()
        if unclaimed_lookup is not None:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT task_id,source_event_id FROM business_goals WHERE status='active'"
                ).fetchall()
            for row in rows:
                if unclaimed_lookup(row["source_event_id"], row["task_id"]) is not None:
                    preserved.add(row["task_id"])
        with self._transaction():
            rows = self._conn.execute("SELECT * FROM business_goals").fetchall()
            for row in rows:
                ledger = self._ledger(row, recover=row["task_id"] not in preserved)
                if row["task_id"] in preserved:
                    ledger.expire_due()
                goal = ledger.get(row["goal_id"])
                if goal.version != row["version"]:
                    self._save(goal, row["version"])
                    changed += 1
        return changed

    def close(self):
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True
