"""Single-runtime durable HTTP admission, dispatch claims and canonical receipts.

The revisioned state is a request dispatch ledger, not the live world model.
Recovery never executes a claimed request. Optional preparation preserves proven
unclaimed requests for the managed publisher. Keep original deadlines
and terminal retention; retain compact tombstones to prevent source ID reuse.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from event.codec import decode_event, from_legacy_event
from event.event_schema import Event
from packages.contracts.actions import ActionIntent, ActionReceipt, finite_json
from packages.contracts.state import StateDelta
from packages.kernel.sqlite_state_repository import checked_state
from packages.kernel.request_action_context import RequestActionContext

MAX_RECORD_BYTES = 524_288
TERMINALS = {"succeeded", "failed", "rejected", "expired", "outcome_unknown"}


class RequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1, max_length=256)
    event_id: str = Field(min_length=1, max_length=256)
    subject_id: str = Field(min_length=1, max_length=256)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event: dict[str, Any] | None
    admitted_at: AwareDatetime
    deadline_at: AwareDatetime
    retention_sec: float = Field(gt=0, allow_inf_nan=False)
    status: Literal["pending", "claimed", "terminal"] = "pending"
    action_id: str | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    terminal_state: (
        Literal["succeeded", "failed", "rejected", "expired", "outcome_unknown"] | None
    ) = None
    ok: bool | None = None
    canonical: dict[str, Any] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.deadline_at <= self.admitted_at:
            raise ValueError("invalid admission deadline")
        if (self.action_id is None) != (self.started_at is None):
            raise ValueError("incomplete request action binding")
        if self.started_at is not None and self.started_at < self.admitted_at:
            raise ValueError("invalid execution time")
        if self.status == "pending" and self.action_id is not None:
            raise ValueError("pending request cannot be claimed")
        if self.status == "claimed" and self.action_id is None:
            raise ValueError("claimed request requires an action")
        if self.status == "terminal":
            if self.completed_at is None or self.completed_at < (
                self.started_at or self.admitted_at
            ):
                raise ValueError("invalid terminal time")
            if self.terminal_state is None or self.ok != (
                self.terminal_state == "succeeded"
            ):
                raise ValueError("invalid terminal outcome")
            if self.canonical is not None:
                validate_receipt(self.canonical, self.task_id, self.event_id)
                if (
                    self.canonical["terminal_state"] != self.terminal_state
                    or self.canonical["ok"] != self.ok
                ):
                    raise ValueError("canonical outcome disagrees with summary")
        elif (
            any(
                v is not None
                for v in (
                    self.completed_at,
                    self.terminal_state,
                    self.ok,
                    self.canonical,
                )
            )
            or self.source_event is None
        ):
            raise ValueError("nonterminal request requires source and no receipt")
        if self.source_event is not None:
            event = decode_event(self.source_event)
            if (
                event.id != self.event_id
                or event.correlation_id != self.task_id
                or source_hash(event) != self.source_hash
            ):
                raise ValueError("request source identity mismatch")
        return self


def source_hash(event):
    return hashlib.sha256(
        finite_json(from_legacy_event(event).model_dump(mode="json")).encode()
    ).hexdigest()


def validate_receipt(receipt, task_id, event_id):
    if (
        not isinstance(receipt, dict)
        or receipt.get("task_id") != task_id
        or receipt.get("event_id") != event_id
    ):
        raise ValueError("request receipt identity mismatch")
    terminal = receipt.get("terminal_state")
    if (
        not isinstance(terminal, str)
        or terminal not in TERMINALS
        or type(receipt.get("ok")) is not bool
        or receipt["ok"] != (terminal == "succeeded")
    ):
        raise ValueError("invalid canonical request outcome")
    finite_json(receipt, 262_144)


class RequestDispatchStore(RequestActionContext):
    REQUEST_SCHEMA = """
    CREATE TABLE IF NOT EXISTS request_dispatch_receipts (
        task_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        schema_version INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','claimed','terminal')),
        record_json TEXT NOT NULL, notify_pending INTEGER NOT NULL DEFAULT 0 CHECK(notify_pending IN (0,1)),
        expires_at REAL, body_retained INTEGER NOT NULL DEFAULT 0 CHECK(body_retained IN (0,1))
    );
    CREATE INDEX IF NOT EXISTS idx_request_dispatch_status ON request_dispatch_receipts(status,task_id);
    CREATE INDEX IF NOT EXISTS idx_request_dispatch_notifications ON request_dispatch_receipts(notify_pending,task_id);
    CREATE INDEX IF NOT EXISTS idx_request_dispatch_retention ON request_dispatch_receipts(body_retained,expires_at,task_id);
    """

    def __init__(
        self,
        db_path,
        *,
        subject_id="eva-001:requests",
        capacity=20_000,
        clock=None,
        enable_action_context=False,
    ):
        if (
            not isinstance(subject_id, str)
            or not 1 <= len(subject_id) <= 256
            or type(capacity) is not int
            or capacity < 1
            or type(enable_action_context) is not bool
        ):
            raise ValueError("invalid request ledger bounds")
        super().__init__(db_path)
        try:
            self._conn.executescript(self.REQUEST_SCHEMA)
            self.subject_id = subject_id
            self.capacity = capacity
            self._clock = clock or (lambda: datetime.now(timezone.utc))
            self._claims: dict[str, str] = {}
            self._agent_claims: dict[str, dict] = {}
            self.enable_action_context = enable_action_context
            self._receipt_sink = None
            self._delivery_errors = 0
            self._resume_prepared = False
            # Derived startup candidates, separate from new HTTP reservations.
            # Reconstruct this connection-local queue from durable proof on restart.
            self._conn.execute(
                "CREATE TEMP TABLE request_resume_candidates(task_id TEXT PRIMARY KEY)"
            )
        except BaseException:
            super().close()
            raise

    def _now(self):
        stamp = self._clock()
        if (
            not isinstance(stamp, datetime)
            or stamp.tzinfo is None
            or stamp.utcoffset() is None
        ):
            raise ValueError("request clock requires UTC-aware datetime")
        return stamp

    def _record(self, task_id):
        row = self._conn.execute(
            "SELECT task_id,event_id,schema_version,status,expires_at,body_retained,CASE WHEN length(CAST(record_json AS BLOB))<=? THEN record_json END AS record_json FROM request_dispatch_receipts WHERE task_id=?",
            (MAX_RECORD_BYTES, task_id),
        ).fetchone()
        if row is None:
            return None
        encoded = row["record_json"]
        if not isinstance(encoded, str):
            raise ValueError("invalid request record size")
        raw = json.loads(encoded)
        if (
            not isinstance(raw, dict)
            or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 1
        ):
            raise ValueError("unsupported request schema")
        finite_json(raw, MAX_RECORD_BYTES)
        record = RequestRecord.model_validate_json(encoded)
        if (record.task_id, record.event_id, record.status, record.schema_version) != (
            row["task_id"],
            row["event_id"],
            row["status"],
            row["schema_version"],
        ) or record.subject_id != self.subject_id:
            raise ValueError("request ledger identity mismatch")
        expires = (
            None
            if record.completed_at is None
            else record.completed_at.timestamp() + record.retention_sec
        )
        if row["expires_at"] != expires or row["body_retained"] != int(
            record.canonical is not None
        ):
            raise ValueError("request retention projection mismatch")
        if record.action_id is not None:
            intent, receipt, _ = self._action(record.action_id)
            if (
                receipt.subject_id != record.subject_id
                or intent.source_event_id != record.event_id
                or intent.slot != "request_processing"
                or intent.tool_id != "runtime:process_event"
                or intent.parameters
                != {
                    "task_id": record.task_id,
                    "source_hash": record.source_hash,
                    "deadline_at": record.deadline_at.isoformat(),
                }
                or receipt.started_at != record.started_at
            ):
                raise ValueError("request dispatch action mismatch")
            envelope = (
                self.source_event_for_action(record.action_id).model_copy(
                    update={"subject_id": decode_event(record.source_event).subject_id}
                )
                if record.source_event
                else None
            )
            if (
                envelope is not None
                and source_hash(decode_event(envelope.model_dump(mode="json")))
                != record.source_hash
            ):
                raise ValueError("request action source mismatch")
        return record

    def _save(self, record):
        encoded = finite_json(record.model_dump(mode="json"), MAX_RECORD_BYTES)
        RequestRecord.model_validate_json(encoded)
        self._conn.execute(
            "UPDATE request_dispatch_receipts SET status=?,record_json=?,expires_at=?,body_retained=? WHERE task_id=?",
            (
                record.status,
                encoded,
                None
                if record.completed_at is None
                else record.completed_at.timestamp() + record.retention_sec,
                int(record.canonical is not None),
                record.task_id,
            ),
        )

    def reserve(self, event, *, timeout_sec, retention_sec):
        for duration in (timeout_sec, retention_sec):
            if (
                isinstance(duration, bool)
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise ValueError("invalid durable request timeout")
        copied = Event.model_validate(event.model_dump())
        if not copied.correlation_id:
            raise ValueError("durable request requires a task ID")
        encoded_source = from_legacy_event(copied).model_dump(mode="json")
        finite_json(encoded_source)
        with self._transaction():
            previous = self._conn.execute(
                "SELECT task_id FROM request_dispatch_receipts WHERE task_id=? OR event_id=?",
                (copied.correlation_id, copied.id),
            ).fetchone()
            if previous is not None:
                record = self._record(previous["task_id"])
                return {
                    "reason": "durable_request_conflict",
                    "task_id": record.task_id,
                    "event_id": record.event_id,
                }
            self._prune()
            retained = self._conn.execute(
                "SELECT COUNT(*) FROM request_dispatch_receipts WHERE status!='terminal' OR body_retained=1"
            ).fetchone()[0]
            if retained >= self.capacity:
                return {"reason": "result_capacity_full"}
            now = self._now()
            record = RequestRecord(
                task_id=copied.correlation_id,
                event_id=copied.id,
                subject_id=self.subject_id,
                source_hash=source_hash(copied),
                source_event=encoded_source,
                admitted_at=now,
                deadline_at=now + timedelta(seconds=timeout_sec),
                retention_sec=float(retention_sec),
            )
            self._conn.execute(
                "INSERT INTO request_dispatch_receipts(task_id,event_id,schema_version,status,record_json,notify_pending) VALUES (?,?,1,'pending',?,0)",
                (
                    record.task_id,
                    record.event_id,
                    finite_json(record.model_dump(mode="json"), MAX_RECORD_BYTES),
                ),
            )
            return {}

    def claim(self, event):
        with self._lock:
            return self._claim(event)

    def _claim(self, event):
        copied = Event.model_validate(event.model_dump())
        with self._transaction():
            record = self._record(copied.correlation_id)
            if record is None:
                return "missing"
            if record.event_id != copied.id or record.source_hash != source_hash(
                copied
            ):
                return "event_mismatch"
            if record.status != "pending":
                return "terminal" if record.status == "terminal" else "running"
            now = self._now()
            if now >= record.deadline_at:
                self._terminal(
                    record,
                    self._failure(record, "request_deadline_exceeded", "expired"),
                    now,
                )
                return "terminal"
            state = self._load(self.subject_id)
            intent = ActionIntent(
                source_event_id=record.event_id,
                slot="request_processing",
                tool_id="runtime:process_event",
                parameters={
                    "task_id": record.task_id,
                    "source_hash": record.source_hash,
                    "deadline_at": record.deadline_at.isoformat(),
                },
            )
            planned = checked_state(
                StateDelta(
                    base_version=state.version,
                    changes={
                        "health": {
                            "request_dispatch": {
                                "task_id": record.task_id,
                                "event_id": record.event_id,
                                "action_id": intent.action_id,
                            }
                        }
                    },
                    source="http_dispatch",
                ).apply(state)
            )
            self._commit(
                state.version,
                planned,
                self._events(
                    [from_legacy_event(copied, subject_id=self.subject_id)],
                    self.subject_id,
                ),
                [intent],
            )
            _, receipt, _ = self._action(intent.action_id)
            token = f"claim_{uuid4().hex}"
            claimed = ActionReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": "executing",
                    "started_at": now,
                    "claim_token": token,
                }
            )
            self._save_receipt(claimed)
            self._save(
                RequestRecord.model_validate(
                    {
                        **record.model_dump(),
                        "status": "claimed",
                        "action_id": intent.action_id,
                        "started_at": now,
                    }
                )
            )
        # Only this process' actual handler lifetime guards close/recovery.
        with self._lock:
            self._claims[record.task_id] = token
            self._active_dispatches += 1
        return "started"

    @staticmethod
    def _failure(record, reason, terminal):
        unknown = terminal == "outcome_unknown"
        return {
            "task_id": record.task_id,
            "event_id": record.event_id,
            "ok": False,
            "terminal_state": terminal,
            "error": reason,
            "execution_state": "outcome_unknown" if unknown else "not_started",
            "reply": "请求执行中断，结果未知。" if unknown else "本次请求未执行。",
            "reply_available": True,
            "selected_agent": "system",
            "duration_ms": 0,
            "mode": (record.source_event or {})
            .get("payload", {})
            .get("mode", "normal"),
            "review": {
                "status": "not_assessed",
                "passed": None,
                "fact_verified": False,
            },
        }

    def _terminal(self, record, receipt, now):
        validate_receipt(receipt, record.task_id, record.event_id)
        if record.status == "terminal":
            return
        self._save(
            RequestRecord.model_validate(
                {
                    **record.model_dump(),
                    "status": "terminal",
                    "completed_at": now,
                    "terminal_state": receipt["terminal_state"],
                    "ok": receipt["ok"],
                    "canonical": receipt,
                }
            )
        )
        self._conn.execute(
            "UPDATE request_dispatch_receipts SET notify_pending=1 WHERE task_id=?",
            (record.task_id,),
        )

    def record_receipt(self, receipt):
        with self._transaction():
            record = self._record(receipt.get("task_id"))
            if record is not None:
                validate_receipt(receipt, record.task_id, record.event_id)
                self._terminal(record, receipt, self._now())
        if self._receipt_sink is not None:
            self.deliver_receipts(self._receipt_sink)

    def end_processing(self, task_id, event_id, *, returned_ok):
        with self._lock:
            token = self._claims.get(task_id)
            if token is None:
                return
            try:
                self.seal_agent_action(task_id)
                record = self._record(task_id)
                if (
                    record is None
                    or record.event_id != event_id
                    or record.action_id is None
                ):
                    raise ValueError("request completion identity mismatch")
                self.finish_action(record.action_id, token, returned_ok=returned_ok)
            finally:
                self._claims.pop(task_id, None)
                self._active_dispatches -= 1

    def summary_for_task(self, task_id, event_id=None):
        with self._lock:
            self._check_open()
            record = self._record(task_id)
            if record is None or record.status != "terminal":
                return None
            if event_id is not None and event_id != record.event_id:
                raise ValueError("request summary event mismatch")
            return {
                "task_id": record.task_id,
                "event_id": record.event_id,
                "terminal_state": record.terminal_state,
                "ok": record.ok,
            }

    def pending_identity(self, task_id):
        """Startup-only identity read for a cross-store recovery lookup."""
        with self._lock:
            self._check_open()
            record = self._record(task_id)
            if record is None:
                raise KeyError(task_id)
            return {"task_id": record.task_id, "event_id": record.event_id}

    def has_request(self, task_id):
        with self._lock:
            self._check_open()
            return self._record(task_id) is not None

    def lookup(self, task_id):
        with self._lock:
            self._check_open()
            record = (
                self._record(task_id)
                if isinstance(task_id, str) and 1 <= len(task_id) <= 256
                else None
            )
            if record is None:
                return {"state": "missing", "payload": None}
            now = self._now()
            if record.status != "terminal" and now >= record.deadline_at:
                with self._transaction():
                    self._terminal(
                        record,
                        self._failure(
                            record,
                            "request_deadline_exceeded",
                            "outcome_unknown"
                            if record.status == "claimed"
                            else "expired",
                        ),
                        now,
                    )
                record = self._record(task_id)
            if record.status != "terminal":
                return {
                    "state": "pending" if record.status == "pending" else "running",
                    "event_id": record.event_id,
                    "payload": None,
                    "timing": {
                        "admission_to_execution_ms": None
                        if record.started_at is None
                        else (record.started_at - record.admitted_at).total_seconds()
                        * 1000,
                        "admission_to_terminal_ms": None,
                        "waiting_age_ms": (now - record.admitted_at).total_seconds()
                        * 1000
                        if record.status == "pending"
                        else None,
                    },
                    "expires_in_sec": max(
                        0.0, (record.deadline_at - now).total_seconds()
                    ),
                }
            if record.canonical is None:
                return {"state": "missing", "payload": None}
            expires = record.completed_at + timedelta(seconds=record.retention_sec)
            if now >= expires:
                return {"state": "missing", "payload": None}
            return {
                "state": "terminal",
                "event_id": record.event_id,
                "payload": record.canonical,
                "timing": {
                    "admission_to_execution_ms": None
                    if record.started_at is None
                    else (record.started_at - record.admitted_at).total_seconds()
                    * 1000,
                    "admission_to_terminal_ms": (
                        record.completed_at - record.admitted_at
                    ).total_seconds()
                    * 1000,
                    "waiting_age_ms": None,
                },
                "expires_in_sec": (expires - now).total_seconds(),
            }

    def recover(self, *, receipt_lookup=None, limit=256):
        return self._recover_page(receipt_lookup=receipt_lookup, limit=limit)[0]

    def prepare_resume(self, *, receipt_lookup=None, limit=256):
        """Startup-only, after the previous owner has exited. No effects here."""
        with self._lock:
            self._check_open()
            if self._active_dispatches or self._resume_prepared:
                raise RuntimeError("request recovery is already prepared or active")
        after = ""
        while True:
            count, cursor = self._recover_page(
                receipt_lookup=receipt_lookup,
                limit=limit,
                resume_pending=True,
                after_task_id=after,
            )
            if cursor is not None:
                after = cursor
            elif count == 0:
                break
        self._resume_prepared = True

    def _recover_page(
        self, *, receipt_lookup=None, limit=256, resume_pending=False, after_task_id=""
    ):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("invalid request recovery bound")
        with self._lock:
            if self._active_dispatches:
                raise RuntimeError("cannot recover active requests")
            rows = self._conn.execute(
                "SELECT task_id FROM request_dispatch_receipts WHERE status!='terminal' AND task_id>? ORDER BY task_id LIMIT ?",
                (after_task_id, limit),
            ).fetchall()
        # Cross-store reads occur before our transaction, never while holding it.
        known = {
            row["task_id"]: receipt_lookup(row["task_id"]) if receipt_lookup else None
            for row in rows
        }
        with self._transaction():
            if self._active_dispatches:
                raise RuntimeError("cannot recover active requests")
            for row in rows:
                record = self._record(row["task_id"])
                if record.status == "terminal":
                    continue
                now = self._now()
                proof = known[record.task_id]
                if proof is not None:
                    validate_receipt(proof, record.task_id, record.event_id)
                    if record.status != "claimed":
                        raise ValueError("unclaimed request has an execution receipt")
                    payload = {
                        **proof,
                        "reply": "",
                        "reply_available": False,
                        "error": "reply_not_retained",
                        "review": {
                            "status": "not_assessed",
                            "passed": None,
                            "fact_verified": False,
                        },
                    }
                elif record.status == "claimed":
                    payload = self._failure(
                        record, "request_interrupted", "outcome_unknown"
                    )
                elif resume_pending and now < record.deadline_at:
                    self._unclaimed_event(record)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO request_resume_candidates VALUES (?)",
                        (record.task_id,),
                    )
                    continue
                else:
                    payload = self._failure(
                        record,
                        "request_deadline_exceeded"
                        if now >= record.deadline_at
                        else "request_interrupted_before_execution",
                        "expired" if now >= record.deadline_at else "rejected",
                    )
                self._terminal(record, payload, now)
            self._prune()
        # Seal our executing actions only. Do not alter other ACT-01 subjects.
        with self._transaction():
            actions = self._conn.execute(
                "SELECT action_id FROM cognitive_action_outbox WHERE subject_id=? AND status='executing' LIMIT ?",
                (self.subject_id, limit),
            ).fetchall()
            for row in actions:
                _, receipt, _ = self._action(row["action_id"])
                self._save_receipt(
                    ActionReceipt.model_validate(
                        {
                            **receipt.model_dump(),
                            "status": "unknown",
                            "observation_kind": "recovery_unknown",
                            "sealed_at": self._now(),
                        }
                    )
                )
        return max(len(rows), len(actions)), rows[-1]["task_id"] if rows else None

    def _unclaimed_event(self, record):
        if (
            record.status != "pending"
            or record.action_id is not None
            or record.source_event is None
        ):
            raise ValueError("request is not unclaimed")
        if (
            self.action_for_source(
                self.subject_id, record.event_id, "request_processing"
            )
            is not None
        ):
            raise ValueError("pending request conflicts with a dispatch intent")
        event = decode_event(record.source_event)
        payload = event.payload
        if (
            event.type != "user_message"
            or event.source != "user"
            or not isinstance(payload.get("text"), str)
            or not 1 <= len(payload["text"]) <= 4000
            or payload.get("mode", "normal") not in {"normal", "deep"}
            or type(payload.get("remember", False)) is not bool
            or type(payload.get("stream", False)) is not bool
        ):
            raise ValueError("unsupported request resume source")
        return event

    def unclaimed_event(self, event_id, task_id):
        """Read-only startup proof for snapshot/goal reconciliation."""
        with self._lock:
            self._check_open()
            selected = self._conn.execute(
                "SELECT task_id FROM request_resume_candidates WHERE task_id=?",
                (task_id,),
            ).fetchone()
            record = self._record(task_id) if selected else None
            if (
                record is None
                or record.status != "pending"
                or self._now() >= record.deadline_at
            ):
                return None
            if record.event_id != event_id:
                raise ValueError("resume request event mismatch")
            return self._unclaimed_event(record)

    def resume_page(self, *, after_task_id="", limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("invalid request resume page bound")
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                "SELECT task_id FROM request_resume_candidates WHERE task_id>? ORDER BY task_id LIMIT ?",
                (after_task_id, limit),
            ).fetchall()
            events = []
            for row in rows:
                record = self._record(row["task_id"])
                if record is not None and record.status == "pending":
                    events.append(self._unclaimed_event(record))
            return {
                "events": events,
                "next_cursor": rows[-1]["task_id"] if rows else None,
            }

    def resume_details(self, event):
        with self._lock:
            original = self.unclaimed_event(event.id, event.correlation_id)
            if original is None:
                return None
            if source_hash(original) != source_hash(event):
                raise ValueError("resume source changed")
            record = self._record(event.correlation_id)
            now = self._now()
            age = (now - record.admitted_at).total_seconds()
            if age < 0:
                raise ValueError("request clock precedes admission")
            return {
                "age_sec": age,
                "remaining_sec": (record.deadline_at - now).total_seconds(),
                "retention_sec": record.retention_sec,
            }

    def acknowledge_resume(self, task_id):
        """A queue handoff only; a crash still recovers durable pending proof."""
        with self._transaction():
            self._conn.execute(
                "DELETE FROM request_resume_candidates WHERE task_id=?", (task_id,)
            )

    def _prune(self):
        # Bounded per sweep; compact identity/outcome tombstones are retained.
        now = self._now()
        rows = self._conn.execute(
            "SELECT task_id FROM request_dispatch_receipts WHERE body_retained=1 AND expires_at<=? ORDER BY expires_at,task_id LIMIT 256",
            (now.timestamp(),),
        ).fetchall()
        for row in rows:
            record = self._record(row["task_id"])
            if now >= record.completed_at + timedelta(seconds=record.retention_sec):
                self._save(
                    RequestRecord.model_validate(
                        {**record.model_dump(), "canonical": None, "source_event": None}
                    )
                )

    def attach_receipt_sink(self, sink):
        self._receipt_sink = sink
        while self.deliver_receipts(sink):
            pass

    @property
    def receipt_sink(self):
        return self._receipt_sink

    def deliver_receipts(self, sink, *, limit=256):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("invalid request notification bound")
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                "SELECT task_id FROM request_dispatch_receipts WHERE notify_pending=1 ORDER BY task_id LIMIT ?",
                (limit,),
            ).fetchall()
            receipts = [self.summary_for_task(row["task_id"]) for row in rows]
        delivered = 0
        for receipt in receipts:
            try:
                sink(receipt)
                with self._transaction():
                    self._conn.execute(
                        "UPDATE request_dispatch_receipts SET notify_pending=0 WHERE task_id=?",
                        (receipt["task_id"],),
                    )
                delivered += 1
            except Exception:
                self._delivery_errors += 1
                raise
        return delivered

    def stats(self):
        with self._lock:
            self._check_open()
            counts = dict(
                self._conn.execute(
                    "SELECT status,COUNT(*) FROM request_dispatch_receipts GROUP BY status"
                ).fetchall()
            )
            return {
                "pending": counts.get("pending", 0),
                "claimed": counts.get("claimed", 0),
                "terminal": counts.get("terminal", 0),
                "active_handlers": self._active_dispatches,
                "capacity": self.capacity,
                "receipt_notifications_pending": self._conn.execute(
                    "SELECT COUNT(*) FROM request_dispatch_receipts WHERE notify_pending=1"
                ).fetchone()[0],
                "receipt_delivery_errors": self._delivery_errors,
                "recovery_replays": False,
                "resume_prepared": self._resume_prepared,
                "action_context_enabled": self.enable_action_context,
                "resume_candidates": self._conn.execute(
                    "SELECT COUNT(*) FROM request_resume_candidates"
                ).fetchone()[0],
            }
