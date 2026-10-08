"""Auditable episode records for event handling and recovery.

An episode links an input event to state revisions, policy/strategy versions,
actions and an observed result.  It is an audit record, not an instruction to
re-run tools; replay returns references and outcomes only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EpisodeSchemaError(ValueError):
    """Malformed or unsupported episode records cannot be replayed."""


class StateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(ge=0)
    integrity_hash: str = Field(default="", max_length=128)


class EpisodeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(
        default_factory=lambda: f"act_{uuid4().hex}", min_length=1, max_length=256
    )
    kind: str = Field(min_length=1, max_length=128)
    status: Literal["planned", "started", "completed", "failed", "unknown"] = "planned"
    tool_id: str = ""
    request_hash: str = ""
    idempotency_key: str = ""
    receipt_id: str = ""
    side_effects: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class EpisodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "succeeded", "failed", "unknown"] = "pending"
    ok: bool | None = None
    summary: str = Field(default="", max_length=2000)
    receipt_id: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    error: str = Field(default="", max_length=2000)


class EpisodeRecord(BaseModel):
    """One bounded, durable processing episode."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 1
    episode_id: str = Field(
        default_factory=lambda: f"ep_{uuid4().hex}", min_length=1, max_length=256
    )
    event_id: str = Field(min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=512)
    subject_id: str = "eva-001"
    correlation_id: str | None = None
    source_event_id: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    state_before: StateReference
    state_after: StateReference | None = None
    policy_version: str = ""
    strategy_version: str = ""
    actions: list[EpisodeAction] = Field(default_factory=list)
    result: EpisodeResult = Field(default_factory=EpisodeResult)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "EpisodeRecord":
        if self.schema_version == 1 and (
            self.state_before.version is None
            or (self.state_after is not None and self.state_after.version is None)
        ):
            raise ValueError("v1 state references require known versions")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("episode started_at requires an explicit UTC offset")
        if self.completed_at is not None:
            if (
                self.completed_at.tzinfo is None
                or self.completed_at.utcoffset() is None
            ):
                raise ValueError("episode completed_at requires an explicit UTC offset")
            if self.completed_at < self.started_at:
                raise ValueError("episode completed_at cannot precede started_at")
        if (
            self.state_after is not None
            and self.state_after.version is not None
            and self.state_before.version is not None
            and self.state_after.version < self.state_before.version
        ):
            raise ValueError("state_after version cannot be older than state_before")
        return self

    def as_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def read_episode(record: dict[str, Any]) -> EpisodeRecord:
    """Parse a stored episode and reject unknown schema versions."""
    if not isinstance(record, dict):
        raise EpisodeSchemaError("episode record must be an object")
    if type(record.get("schema_version")) is not int or record.get(
        "schema_version"
    ) not in (1, 2):
        raise EpisodeSchemaError("unsupported episode schema_version")
    try:
        return EpisodeRecord.model_validate(record)
    except ValueError as exc:
        raise EpisodeSchemaError(str(exc)) from exc


def replay_projection(episode: EpisodeRecord | dict[str, Any]) -> dict[str, Any]:
    """Return a side-effect-free audit projection for recovery and inspection."""
    item = episode if isinstance(episode, EpisodeRecord) else read_episode(episode)
    return {
        "episode_id": item.episode_id,
        "event_id": item.event_id,
        "event_type": item.event_type,
        "subject_id": item.subject_id,
        "state_before": item.state_before.model_dump(mode="json"),
        "state_after": item.state_after.model_dump(mode="json")
        if item.state_after
        else None,
        "policy_version": item.policy_version,
        "strategy_version": item.strategy_version,
        "action_ids": [action.action_id for action in item.actions],
        "result": item.result.model_dump(mode="json"),
        "replay_effect": "rebuild_state_only",
        "external_actions_replayed": False,
    }


__all__ = [
    "EpisodeAction",
    "EpisodeRecord",
    "EpisodeResult",
    "EpisodeSchemaError",
    "StateReference",
    "read_episode",
    "replay_projection",
]
