"""Durable planned actions and handler observations, separate from business success."""

from datetime import datetime
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def finite_json(value: Any, limit: int = 65_536) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("durable action record exceeds byte limit")
    return encoded


class ActionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    action_id: str = Field(
        default_factory=lambda: f"act_{uuid4().hex}", min_length=1, max_length=256
    )
    source_event_id: str = Field(min_length=1, max_length=256)
    slot: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value):
        finite_json(value, 32_768)
        return value

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(
            finite_json(self.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()


class ActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=256)
    subject_id: str = Field(min_length=1, max_length=256)
    source_event_id: str = Field(min_length=1, max_length=256)
    state_version: int = Field(ge=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "executing", "completed", "unknown"] = "pending"
    claim_token: str | None = Field(default=None, min_length=1, max_length=256)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sealed_at: datetime | None = None
    observation_kind: Literal[
        "intent", "handler_return", "handler_exception", "recovery_unknown"
    ] = "intent"
    returned_ok: bool | None = None

    @model_validator(mode="after")
    def coherent_receipt(self):
        for stamp in (self.started_at, self.finished_at, self.sealed_at):
            if stamp is not None and (
                stamp.tzinfo is None or stamp.utcoffset() is None
            ):
                raise ValueError("action timestamps require timezone")
        if self.status == "pending":
            if (
                any(
                    value is not None
                    for value in (
                        self.claim_token,
                        self.started_at,
                        self.finished_at,
                        self.sealed_at,
                        self.returned_ok,
                    )
                )
                or self.observation_kind != "intent"
            ):
                raise ValueError("pending intent has no execution observation")
        elif self.claim_token is None or self.started_at is None:
            raise ValueError("claimed action requires token and start time")
        elif self.status == "executing":
            if (
                self.finished_at is not None
                or self.sealed_at is not None
                or self.returned_ok is not None
                or self.observation_kind != "intent"
            ):
                raise ValueError("executing action has no terminal observation")
        elif self.observation_kind == "recovery_unknown":
            if (
                self.status != "unknown"
                or self.finished_at is not None
                or self.returned_ok is not None
                or self.sealed_at is None
                or self.sealed_at < self.started_at
            ):
                raise ValueError("recovery cannot invent a handler return")
        elif (
            self.finished_at is None
            or self.finished_at < self.started_at
            or self.sealed_at is not None
            or self.observation_kind == "intent"
        ):
            raise ValueError("invalid handler observation")
        elif self.status != (
            "completed" if self.returned_ok is True else "unknown"
        ) or (
            self.observation_kind == "handler_exception"
            and self.returned_ok is not None
        ):
            raise ValueError("handler observation does not match status")
        return self


def read_action_record(encoded: str, model):
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 65_536:
        raise ValueError("invalid durable action record size")
    raw = json.loads(encoded)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("unsupported durable action record version")
    finite_json(raw)
    return model.model_validate_json(encoded)
