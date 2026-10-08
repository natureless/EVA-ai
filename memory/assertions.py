"""Temporal assertions with explicit source and lifecycle semantics.

An assertion is a time-scoped claim, not a proof of truth.  The model keeps
observation time separate from the period in which a claim is intended to be
valid, and makes retraction/supersession visible instead of deleting history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory.provenance import EpistemicStatus, provenance, read_provenance


class AssertionSchemaError(ValueError):
    """Malformed or unsupported assertion records must not become facts."""


class AssertionStatus(str, Enum):
    ACTIVE = "active"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("assertion timestamps require an explicit UTC offset")
    return value.astimezone(timezone.utc)


class TemporalAssertion(BaseModel):
    """Versioned claim with bounded time validity and source references."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = 1
    assertion_id: str = Field(default_factory=lambda: f"asrt_{uuid4().hex}", min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=512)
    predicate: str = Field(min_length=1, max_length=256)
    value: Any
    status: AssertionStatus = AssertionStatus.ACTIVE

    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    source: str = ""
    source_event_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=lambda: provenance())

    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    retracts: list[str] = Field(default_factory=list)
    retracted_at: datetime | None = None
    retracted_by: str = ""
    lifecycle_reason: str = ""

    @model_validator(mode="after")
    def validate_temporal_bounds(self) -> "TemporalAssertion":
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _utc(self.valid_from))
        if self.valid_until is not None:
            object.__setattr__(self, "valid_until", _utc(self.valid_until))
        if self.retracted_at is not None:
            object.__setattr__(self, "retracted_at", _utc(self.retracted_at))
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        try:
            json.dumps(self.value, ensure_ascii=False, allow_nan=False)
            json.dumps(self.provenance, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("assertion value and provenance must be finite JSON") from exc
        return self

    def is_current(self, at: datetime | None = None) -> bool:
        """Return whether this assertion is active at the requested instant."""
        if self.status is not AssertionStatus.ACTIVE:
            return False
        point = _utc(at or datetime.now(timezone.utc))
        if self.valid_from and point < self.valid_from:
            return False
        if self.valid_until and point >= self.valid_until:
            return False
        return True

    def retract(
        self,
        *,
        by_event_id: str,
        reason: str,
        at: datetime | None = None,
    ) -> "TemporalAssertion":
        """Return a historical copy marked retracted; never delete the claim."""
        if not by_event_id or not reason:
            raise ValueError("retraction requires by_event_id and reason")
        return self.model_copy(update={
            "status": AssertionStatus.RETRACTED,
            "retracted_at": _utc(at or datetime.now(timezone.utc)),
            "retracted_by": by_event_id,
            "lifecycle_reason": reason[:1000],
        })

    def supersede(
        self,
        *,
        replacement_id: str,
        by_event_id: str,
        reason: str = "",
        at: datetime | None = None,
    ) -> "TemporalAssertion":
        """Return a historical copy linked to its replacement assertion."""
        if not replacement_id or not by_event_id:
            raise ValueError("supersession requires replacement_id and by_event_id")
        return self.model_copy(update={
            "status": AssertionStatus.SUPERSEDED,
            "superseded_by": replacement_id,
            "retracted_at": _utc(at or datetime.now(timezone.utc)),
            "retracted_by": by_event_id,
            "lifecycle_reason": reason[:1000],
        })

    def as_record(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible record for SQLite or graph storage."""
        return self.model_dump(mode="json")


def make_assertion(
    *,
    subject: str,
    predicate: str,
    value: Any,
    source: str,
    source_event_id: str = "",
    observed_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    evidence_ids: list[str] | None = None,
    epistemic_status: Any = None,
) -> TemporalAssertion:
    """Construct an assertion while preserving the existing provenance labels."""
    status = epistemic_status if epistemic_status is not None else EpistemicStatus.UNKNOWN
    if isinstance(status, str):
        status = EpistemicStatus(status)
    origin = provenance(
        status,
        source=source,
        source_event_id=source_event_id,
        evidence_ids=evidence_ids,
    )
    return TemporalAssertion(
        subject=subject,
        predicate=predicate,
        value=value,
        source=source,
        source_event_id=source_event_id,
        observed_at=observed_at or datetime.now(timezone.utc),
        valid_from=valid_from,
        valid_until=valid_until,
        evidence_ids=list(dict.fromkeys(evidence_ids or []))[:16],
        provenance=origin,
    )


def read_assertion(record: dict[str, Any]) -> TemporalAssertion:
    """Read an assertion without upgrading unsupported history to active."""
    raw = record.get("assertion", record)
    if not isinstance(raw, dict):
        raise AssertionSchemaError("assertion record must be an object")
    data = dict(raw)
    version = data.get("schema_version", 0)
    if version != 1:
        data["schema_version"] = 1
        data["status"] = AssertionStatus.UNKNOWN
        data["provenance"] = provenance()
    data.setdefault("provenance", read_provenance({"provenance": data.get("provenance", {})}))
    try:
        return TemporalAssertion.model_validate(data)
    except ValueError as exc:
        raise AssertionSchemaError(str(exc)) from exc


def current_assertions(
    records: list[dict[str, Any] | TemporalAssertion],
    *,
    at: datetime | None = None,
) -> list[TemporalAssertion]:
    """Filter assertions by lifecycle and validity without merging conflicts."""
    parsed = [
        record if isinstance(record, TemporalAssertion) else read_assertion(record)
        for record in records
    ]
    return [record for record in parsed if record.is_current(at)]


__all__ = [
    "AssertionSchemaError",
    "AssertionStatus",
    "TemporalAssertion",
    "current_assertions",
    "make_assertion",
    "read_assertion",
]
