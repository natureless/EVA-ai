"""Append-only file reconciliation. No handler invocation or outcome promotion."""

import json
from uuid import uuid4

from memory.tool_observations import (
    ToolFileIntent,
    ToolObservation,
    read_tool_observation,
)
from packages.minimal_brain.file_verifier import FileVerificationSpec


SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_tool_file_intents (
    action_id TEXT PRIMARY KEY REFERENCES episode_tool_receipts(action_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_tool_observations (
    action_id TEXT NOT NULL REFERENCES episode_tool_file_intents(action_id),
    sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 32),
    observation_id TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    PRIMARY KEY(action_id,sequence)
);
"""


def intent_for(connection, receipt):
    row = connection.execute(
        "SELECT CASE WHEN length(CAST(record_json AS BLOB))<=8192 THEN record_json END AS record_json FROM episode_tool_file_intents WHERE action_id=?",
        (receipt.action_id,),
    ).fetchone()
    if row is None:
        return None
    intent = read_tool_observation(row["record_json"], intent=True)
    if (
        any(
            getattr(intent, key) != getattr(receipt, key)
            for key in ("action_id", "event_id", "receipt_id", "request_hash")
        )
        or receipt.tool_id != "executor:file:write"
    ):
        raise ValueError("file intent identity mismatch")
    FileVerificationSpec.model_validate(spec_for(intent))
    return intent


def spec_for(intent):
    return {
        "kind": intent.verifier_id,
        "files": [{"path": intent.path, "sha256": intent.expected_sha256}],
    }


def history_for(connection, receipt):
    intent = intent_for(connection, receipt)
    if intent is None:
        return {"status": "unsupported", "count": 0, "records": []}
    rows = connection.execute(
        "SELECT sequence,observation_id,CASE WHEN length(CAST(record_json AS BLOB))<=8192 THEN record_json END AS record_json FROM episode_tool_observations WHERE action_id=? ORDER BY sequence LIMIT 33",
        (receipt.action_id,),
    ).fetchall()
    if len(rows) > 32:
        raise ValueError("tool observation limit exceeded")
    records = []
    for sequence, row in enumerate(rows, 1):
        record = read_tool_observation(row["record_json"])
        if (
            row["sequence"] != sequence
            or record.sequence != sequence
            or record.observation_id != row["observation_id"]
            or record.started_at < receipt.started_at
            or any(
                getattr(record, key) != value
                for key, value in intent.model_dump().items()
            )
        ):
            raise ValueError("tool observation identity mismatch")
        records.append(record.model_dump(mode="json"))
    return {
        "status": "available",
        "count": len(records),
        "records": records,
        "intent": intent.model_dump(mode="json"),
    }


class ToolReconciliation:
    """Mixin using the journal lock/transaction and the injected read-only verifier."""

    def _capture_file_intent(self, receipt, args):
        if self.file_verifier is None or receipt.tool_id != "executor:file:write":
            return
        spec = self.file_verifier.capture_write(args)
        if spec is None:
            return
        file = spec["files"][0]
        intent = ToolFileIntent(
            action_id=receipt.action_id,
            event_id=receipt.event_id,
            receipt_id=receipt.receipt_id,
            request_hash=receipt.request_hash,
            workspace_hash=self.file_verifier.workspace_hash,
            path=file["path"],
            expected_sha256=file["sha256"],
        )
        self._conn.execute(
            "INSERT INTO episode_tool_file_intents VALUES (?,?)",
            (receipt.action_id, intent.model_dump_json()),
        )

    def _reconciliation_receipt(self, event_id, action_id):
        if self._closed:
            raise RuntimeError("processing Episode store is closed")
        receipt = self._tool_receipt(action_id)
        if receipt.event_id != event_id:
            raise ValueError("tool observation event mismatch")
        return receipt

    def observation_history(self, event_id, action_id):
        with self._lock:
            return history_for(
                self._conn, self._reconciliation_receipt(event_id, action_id)
            )

    def reconcile_tool(self, event_id, action_id, *, expected_count):
        if type(expected_count) is not int or not 0 <= expected_count < 32:
            raise ValueError("invalid tool observation count")
        if self.file_verifier is None:
            raise RuntimeError("tool file verifier unavailable")
        with self._transaction():
            receipt = self._reconciliation_receipt(event_id, action_id)
            if receipt.status != "unknown":
                raise ValueError("only unknown tool outcomes may be reconciled")
            intent = intent_for(self._conn, receipt)
            history = history_for(self._conn, receipt)
            if intent is None or history["count"] != expected_count:
                raise ValueError("unsupported tool or stale observation count")
            if intent.workspace_hash != self.file_verifier.workspace_hash:
                raise ValueError("file verification workspace changed")
        # File I/O never holds a database write transaction or the journal lock.
        sample = self.file_verifier.run(spec_for(intent))
        if sample["verifier_id"] != intent.verifier_id:
            raise ValueError("file verifier identity mismatch")
        observation = ToolObservation.model_validate_json(
            json.dumps(
                {
                    **intent.model_dump(mode="json"),
                    "observation_id": f"to_{uuid4().hex}",
                    "sequence": expected_count + 1,
                    "scope": sample["scope"],
                    "started_at": sample["started_at"],
                    "observed_at": sample["observed_at"],
                    "outcome": sample["outcome"],
                    "files": sample["files"],
                }
            )
        )
        if observation.started_at < receipt.started_at:
            raise ValueError("file observation predates invocation")
        with self._transaction():
            current = self._reconciliation_receipt(event_id, action_id)
            if (
                current != receipt
                or intent_for(self._conn, current) != intent
                or history_for(self._conn, current)["count"] != expected_count
            ):
                raise ValueError("tool observation binding changed")
            self._conn.execute(
                "INSERT INTO episode_tool_observations VALUES (?,?,?,?)",
                (
                    action_id,
                    observation.sequence,
                    observation.observation_id,
                    observation.model_dump_json(),
                ),
            )
        return observation.model_dump(mode="json")
