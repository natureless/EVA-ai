"""Explicit event upgrades and lossless supported-type envelope conversion."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from event.contracts import EventEnvelope, EventFamily
from event.event_schema import Event

LEGACY_EVENT_MAP = {
    "user_message": EventFamily.PERCEPTION.USER_MESSAGE,
    "system_tick": EventFamily.PERCEPTION.SCHEDULER_TICK,
    "maintenance": EventFamily.LIFECYCLE.MAINTENANCE_STARTED,
    "reminder_trigger": EventFamily.PERCEPTION.SYSTEM_EVENT,
    "github_push": EventFamily.PERCEPTION.GITHUB_PUSH,
    "github_pr": EventFamily.PERCEPTION.GITHUB_PR,
    "github_issue": EventFamily.PERCEPTION.GITHUB_ISSUE,
    "github_workflow": EventFamily.PERCEPTION.GITHUB_WORKFLOW,
}
ENVELOPE_EVENT_MAP = {value: key for key, value in LEGACY_EVENT_MAP.items()}
# Historical MVSC conversions used this prefix for GitHub events.
ENVELOPE_EVENT_MAP.update({f"legacy.{kind}": kind for kind in LEGACY_EVENT_MAP if kind.startswith("github_")})


class UnsupportedEvent(ValueError):
    pass


def source_event_identity(source: str, event_type: str, source_event_id: str, *, subject_id: str = "eva-001") -> str:
    """Stable source occurrence identity, not a deduplication/execution guarantee."""
    if not all(isinstance(value, str) and value for value in (source, event_type, source_event_id, subject_id)):
        raise ValueError("source identity requires nonempty string components")
    key = json.dumps(["eva.event.v1", subject_id, source, event_type, source_event_id], ensure_ascii=False, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, key))


def from_legacy_event(legacy_event: Event, subject_id: str | None = None) -> EventEnvelope:
    event = Event.model_validate(legacy_event.model_dump())
    data = event.model_dump()
    data["event_id"] = data.pop("id")
    data["event_type"] = LEGACY_EVENT_MAP[data.pop("type")]
    if subject_id is not None:
        data["subject_id"] = subject_id
    return EventEnvelope.model_validate(data)


def to_legacy_event(envelope: EventEnvelope) -> Event:
    envelope = EventEnvelope.model_validate(envelope.model_dump())
    if envelope.event_type not in ENVELOPE_EVENT_MAP:
        raise UnsupportedEvent(f"event type has no stable consumer: {envelope.event_type}")
    data = envelope.model_dump()
    data["id"] = data.pop("event_id")
    original_type = data.pop("event_type")
    data["type"] = ENVELOPE_EVENT_MAP[original_type]
    if original_type.startswith("legacy."):
        data["compatibility"] = {**data["compatibility"], "original_event_type": original_type}
    return Event.model_validate(data)


def decode_event(record: Mapping[str, Any]) -> Event:
    """Read v1 envelopes or unversioned stable rows. Never invent ID/time on read.

    A null event_contract column denotes an old row. Explicit unknown versions
    and malformed contracts fail rather than falling back to the legacy columns.
    """
    data = deepcopy(dict(record))
    contract = data.pop("event_contract", None)
    if contract is not None:
        raw = json.loads(contract) if isinstance(contract, str) else contract
        if not isinstance(raw, dict) or "schema_version" not in raw:
            raise UnsupportedEvent("stored event contract requires an explicit schema_version")
        decoded = decode_event(raw)
        # Status is a storage projection, not part of immutable source identity.
        for key in ("id", "type", "source", "correlation_id"):
            if key in data and data[key] != getattr(decoded, key):
                raise UnsupportedEvent(f"event contract disagrees with stored {key}")
        if "payload" in data:
            payload = json.loads(data["payload"]) if isinstance(data["payload"], str) else data["payload"]
            if payload != decoded.payload:
                raise UnsupportedEvent("event contract disagrees with stored payload")
        if "timestamp" in data:
            stamp = data["timestamp"]
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else stamp
            if stamp.tzinfo is None and decoded.compatibility.get("timestamp_assumed_utc") is True:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp != decoded.timestamp:
                raise UnsupportedEvent("event contract disagrees with stored timestamp")
        return decoded
    versioned = "schema_version" in data
    if versioned and data["schema_version"] != "1.0":
        raise UnsupportedEvent(f"unsupported event schema_version: {data['schema_version']}")
    envelope = "event_type" in data or "event_id" in data
    for key in (("event_id", "event_type", "source", "timestamp") if envelope else ("id", "type", "source", "timestamp")):
        if key not in data:
            raise UnsupportedEvent(f"stored event requires {key}")
    if envelope and not versioned:
        raise UnsupportedEvent("envelope requires an explicit schema_version")
    if isinstance(data.get("payload"), str):
        data["payload"] = json.loads(data["payload"])
    if not versioned:
        data["compatibility"] = {**data.get("compatibility", {}), "source_schema": "unversioned-stable"}
        stamp = data["timestamp"]
        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else stamp
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            # Historical storage used UTC; retain the explicit assumption marker.
            stamp = stamp.replace(tzinfo=timezone.utc)
            data["compatibility"]["timestamp_assumed_utc"] = True
        data["timestamp"] = stamp
        data["schema_version"] = "1.0"
    return to_legacy_event(EventEnvelope.model_validate(data)) if envelope else Event.model_validate(data)


def encode_event(event: Event) -> str:
    return from_legacy_event(event).model_dump_json()
