"""Bounded tool-call observations; they do not certify external side effects."""

from datetime import datetime
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnresolvedToolCall(ValueError):
    """An identical call already has an unresolved outcome in this Episode."""


class ToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=256)
    receipt_id: str = Field(min_length=1, max_length=256)
    event_id: str = Field(min_length=1, max_length=256)
    loop_id: str = Field(min_length=1, max_length=256)
    parent_action_id: str = Field(min_length=1, max_length=256)
    tool_id: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["started", "completed", "unknown"] = "started"
    observation_kind: Literal[
        "intent", "handler_return", "handler_exception", "seal_unknown"
    ] = "intent"
    returned_ok: bool | None = None
    started_at: datetime
    finished_at: datetime | None = None
    sealed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_observation(self):
        for stamp in (self.started_at, self.finished_at, self.sealed_at):
            if stamp is not None and (
                stamp.tzinfo is None
                or stamp.utcoffset() is None
                or stamp < self.started_at
            ):
                raise ValueError("invalid tool receipt timestamp")
        if self.status == "completed" and (
            self.returned_ok is not True or self.observation_kind != "handler_return"
        ):
            raise ValueError("completed requires an observed successful handler return")
        if self.observation_kind == "intent":
            if (
                self.status != "started"
                or self.returned_ok is not None
                or self.finished_at is not None
                or self.sealed_at is not None
            ):
                raise ValueError("intent cannot claim an outcome")
        elif self.observation_kind == "seal_unknown":
            if (
                self.status != "unknown"
                or self.returned_ok is not None
                or self.finished_at is not None
                or self.sealed_at is None
            ):
                raise ValueError("interruption cannot claim a handler return")
        elif (
            self.finished_at is None
            or self.sealed_at is not None
            or self.status == "started"
        ):
            raise ValueError("handler observation requires a finish time")
        elif self.status != (
            "completed" if self.returned_ok is True else "unknown"
        ) or (
            self.observation_kind == "handler_exception"
            and self.returned_ok is not None
        ):
            raise ValueError("handler outcome is inconsistent")
        return self


def read_tool_receipt(encoded: str) -> ToolReceipt:
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 8192:
        raise ValueError("tool receipt exceeds record limit")
    raw = json.loads(encoded)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("unsupported tool receipt version")
    return ToolReceipt.model_validate_json(encoded)
