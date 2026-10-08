"""Versioned knowledge labels. A source records origin, not proof of truth."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any


class EpistemicStatus(str, Enum):
    VERIFIED_FACT = "verified_fact"
    USER_STATEMENT = "user_statement"
    WORKING_MODEL = "working_model"
    HYPOTHESIS = "hypothesis"
    ASSISTANT_INFERENCE = "assistant_inference"
    TOOL_OBSERVATION = "tool_observation"
    SIMULATION = "simulation"
    UNKNOWN = "unknown"


def provenance(
    status: EpistemicStatus = EpistemicStatus.UNKNOWN,
    *,
    source: str = "",
    source_event_id: str = "",
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "epistemic_status": status.value,
        "source": source,
        "source_event_id": source_event_id,
    }
    if evidence_ids is not None:
        result["evidence_ids"] = list(dict.fromkeys(
            value[:160] for value in evidence_ids if isinstance(value, str) and value
        ))[:16]
    return result


def read_provenance(record: dict[str, Any], *, source_fallback: bool = True) -> dict[str, Any]:
    """Read new or legacy rows without upgrading unlabeled history to fact."""
    raw = record.get("provenance", record.get("provenance_json", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        status = EpistemicStatus(raw.get("epistemic_status", "unknown"))
    except (ValueError, TypeError):
        status = EpistemicStatus.UNKNOWN
    result = provenance(
        status,
        source=str(raw.get("source") or (record.get("source") if source_fallback else "") or ""),
        source_event_id=str(raw.get("source_event_id") or record.get("source_event_id") or ""),
        evidence_ids=raw.get("evidence_ids") if isinstance(raw.get("evidence_ids"), list) else None,
    )
    version = raw.get("schema_version", 1)
    if type(version) is not int or version != 1:
        result["epistemic_status"] = EpistemicStatus.UNKNOWN.value
    return result


def field_provenance(record: dict[str, Any], fields: list[str]) -> dict[str, dict]:
    """Read origins for current fields, falling back to the record's origin."""
    raw = record.get("provenance", record.get("provenance_json", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    version = raw.get("schema_version", 1)
    if type(version) is not int or version != 1:
        return {key: provenance() for key in fields}
    origins = record.get("field_provenance", raw.get("field_provenance", {}))
    origins = origins if isinstance(origins, dict) else {}
    fallback = read_provenance(record)
    return {key: read_provenance({"provenance": origins.get(key, fallback)}) for key in fields}


def aggregate_provenance(origins: dict[str, dict]) -> dict:
    """Mixed current fields are unknown as a whole, never automatically facts."""
    values = [read_provenance({"provenance": value}) for value in origins.values()]
    if values and all(value == values[0] for value in values):
        return values[0]
    return provenance(source="mixed" if values else "")


def world_context_line(record: dict[str, Any]) -> str:
    """Quote a bounded world record with its field origins, not as instructions."""
    payload = {key: str(record[key])[:160] for key in ("id", "name", "status", "priority", "deadline") if key in record}
    origin = read_provenance(record)
    fields = ["name"] + [f"properties.{key}" for key in ("status", "priority", "deadline") if key in payload]
    origins = field_provenance(record, fields)
    for item in [origin, *origins.values()]:
        item["source"] = item["source"][:160]
        item["source_event_id"] = item["source_event_id"][:160]
    return json.dumps({**payload, "provenance": origin, "field_provenance": origins}, ensure_ascii=False)


def memory_context_line(record: dict[str, Any], limit: int = 200) -> str:
    """JSON quoting keeps memory content visually separate from instructions."""
    origin = read_provenance(record)
    payload = {
        "id": record.get("id", record.get("key", "")),
        **origin,
        "content": str(record.get("content", ""))[:limit],
    }
    # Persist the full schema, but do not spend prompt tokens on empty locators.
    return json.dumps(
        {key: value for key, value in payload.items() if value != "" and key != "schema_version"},
        ensure_ascii=False,
    )
