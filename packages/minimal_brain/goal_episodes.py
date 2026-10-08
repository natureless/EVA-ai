"""Bounded references to existing EPI-01 rows; never synthesize or append episodes."""

import json
import sqlite3

from memory.episodes import read_episode
from memory.tool_receipts import read_tool_receipt
from packages.kernel.tool_reconciliation import history_for

MAX_EPISODE_BYTES = 65_536
MAX_ACTIONS = 16


def _tool_projection(connection, record):
    actions = [a for a in record.actions[:MAX_ACTIONS] if a.kind == "tool_invocation"]
    if not actions:
        return {"status": "not_applicable", "receipts": []}
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episode_tool_receipts'"
        ).fetchone():
            return {"status": "not_recorded", "receipts": []}
        receipts = []
        for action in actions:
            row = connection.execute(
                "SELECT action_id,event_id,receipt_id,CASE WHEN length(CAST(record_json AS BLOB))<=8192 THEN record_json END AS record_json FROM episode_tool_receipts WHERE action_id=?",
                (action.action_id,),
            ).fetchone()
            if row is None:
                return {"status": "not_recorded", "receipts": []}
            receipt = read_tool_receipt(row["record_json"])
            if (
                receipt.action_id != action.action_id
                or row["action_id"] != action.action_id
                or receipt.event_id != record.event_id
                or row["event_id"] != record.event_id
                or receipt.receipt_id != action.receipt_id
                or row["receipt_id"] != action.receipt_id
                or receipt.tool_id != action.tool_id
                or receipt.request_hash != action.request_hash
                or receipt.loop_id != record.metadata.get("loop_id")
                or receipt.status != action.status
                or receipt.started_at != action.started_at
                or receipt.finished_at != action.completed_at
                or not any(
                    a.action_id == receipt.parent_action_id for a in record.actions
                )
            ):
                raise ValueError("tool receipt identity mismatch")
            projected = receipt.model_dump(mode="json", exclude={"loop_id"})
            try:
                observations = history_for(connection, receipt)
                projected["reconciliation"] = {
                    **observations,
                    "records": observations["records"][-3:],
                    "truncated": observations["count"] > 3,
                }
            except (sqlite3.DatabaseError, ValueError, TypeError, RecursionError):
                projected["reconciliation"] = {
                    "status": "unavailable",
                    "count": 0,
                    "records": [],
                }
            receipts.append(projected)
        return {"status": "available", "receipts": receipts}
    except (sqlite3.DatabaseError, ValueError, TypeError, RecursionError):
        return {"status": "unavailable", "receipts": []}


def read_goal_episode(connection, event_id):
    """Use the caller's read transaction and link only consistent stored identities."""

    def unavailable(reason):
        return {"status": "unavailable", "reason": reason, "episode": None}

    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes'"
        ).fetchone()
        if not exists:
            return {
                "status": "not_recorded",
                "reason": "store_missing",
                "episode": None,
            }
        rows = connection.execute(
            """SELECT episode_id,event_id,subject_id,
                CASE WHEN length(CAST(record_json AS BLOB)) <= ? THEN record_json END AS record_json
                FROM episodes WHERE event_id=? LIMIT 2""",
            (MAX_EPISODE_BYTES, event_id),
        ).fetchall()
    except sqlite3.DatabaseError:
        return unavailable("read_failed")
    if not rows:
        return {"status": "not_recorded", "reason": "event_missing", "episode": None}
    if len(rows) != 1:
        return unavailable("ambiguous_event")
    row = rows[0]
    if row["record_json"] is None:
        return unavailable("oversized_or_invalid")
    try:
        raw = json.loads(row["record_json"])
        if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int:
            return unavailable("invalid_record")
        record = read_episode(raw)
    except (ValueError, TypeError, RecursionError):
        return unavailable("invalid_record")
    if (
        record.event_id != event_id
        or record.event_id != row["event_id"]
        or record.episode_id != row["episode_id"]
        or record.subject_id != row["subject_id"]
    ):
        return unavailable("identity_mismatch")
    # Audit structure only: raw observations, metadata, model text and tool payloads
    # are deliberately outside this projection, and cannot become business evidence.
    actions = [
        {
            "action_id": a.action_id,
            "kind": a.kind,
            "status": a.status,
            "receipt_id": a.receipt_id[:256],
        }
        for a in record.actions[:MAX_ACTIONS]
    ]
    completion_kind = record.metadata.get("completion_kind")
    projection = {
        "schema_version": 1,
        "episode_schema_version": record.schema_version,
        "episode_id": record.episode_id,
        "event_id": record.event_id,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat()
        if record.completed_at
        else None,
        "state_before": record.state_before.model_dump(mode="json"),
        "state_after": record.state_after.model_dump(mode="json")
        if record.state_after
        else None,
        "policy_version": record.policy_version[:128],
        "strategy_version": record.strategy_version[:128],
        "result_status": record.result.status,
        "result_receipt_id": record.result.receipt_id[:256],
        "actions": actions,
        "action_count": len(record.actions),
        "actions_truncated": len(record.actions) > MAX_ACTIONS,
        "fields_truncated": len(record.policy_version) > 128
        or len(record.strategy_version) > 128
        or len(record.result.receipt_id) > 256
        or any(len(a.receipt_id) > 256 for a in record.actions[:MAX_ACTIONS]),
        "external_actions_replayed": False,
        "tool_receipts": _tool_projection(connection, record),
        "state_reference_kind": "world_snapshot_unversioned"
        if record.metadata.get("state_reference_kind") == "world_snapshot_unversioned"
        else "state_revision",
        "completion_kind": completion_kind
        if isinstance(completion_kind, str)
        and completion_kind in {"canonical_receipt", "recovery_unknown"}
        else None,
    }
    try:
        json.dumps(projection, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError):
        return unavailable("invalid_record")
    return {"status": "available", "reason": None, "episode": projection}
