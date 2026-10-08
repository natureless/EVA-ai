"""Independent file samples bound to immutable tool intents, never execution outcomes."""

from datetime import datetime
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolFileIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=256)
    event_id: str = Field(min_length=1, max_length=256)
    receipt_id: str = Field(min_length=1, max_length=256)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_id: Literal["workspace_files_sha256_v1"] = "workspace_files_sha256_v1"
    path: str = Field(min_length=1, max_length=256)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ToolFileSample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=256)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0, le=2 * 1024 * 1024)
    outcome: Literal["match", "mismatch", "missing", "unknown"]
    reason: (
        Literal[
            "unsafe_path",
            "not_regular_file",
            "file_too_large",
            "file_changed",
            "read_error",
        ]
        | None
    ) = None
    observed_at: datetime

    @model_validator(mode="after")
    def coherent_sample(self):
        if self.outcome in {"match", "mismatch"}:
            if (
                self.observed_sha256 is None
                or self.size_bytes is None
                or self.reason is not None
            ):
                raise ValueError("hash sample requires digest and size")
            if (self.observed_sha256 == self.expected_sha256) != (
                self.outcome == "match"
            ):
                raise ValueError("sample outcome does not match digests")
        elif self.observed_sha256 is not None or self.size_bytes is not None:
            raise ValueError("unobserved file cannot claim bytes")
        elif (self.reason is not None) != (self.outcome == "unknown"):
            raise ValueError("unknown sample requires a reason")
        return self


class ToolObservation(ToolFileIntent):
    observation_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1, le=32)
    scope: Literal["file_content_samples"] = "file_content_samples"
    started_at: datetime
    observed_at: datetime
    outcome: Literal["passed", "mismatch", "unknown"]
    files: list[ToolFileSample] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def coherent_observation(self):
        sample = self.files[0]
        expected = (
            "passed"
            if sample.outcome == "match"
            else "unknown"
            if sample.outcome == "unknown"
            else "mismatch"
        )
        if (
            sample.path != self.path
            or sample.expected_sha256 != self.expected_sha256
            or self.outcome != expected
        ):
            raise ValueError("observation does not match intent")
        for stamp in (self.started_at, sample.observed_at, self.observed_at):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("observation requires aware timestamps")
        if not self.started_at <= sample.observed_at <= self.observed_at:
            raise ValueError("invalid observation chronology")
        return self


def read_tool_observation(encoded: str, *, intent=False):
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 8192:
        raise ValueError("tool observation exceeds record limit")
    raw = json.loads(encoded)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("unsupported tool observation version")
    return (ToolFileIntent if intent else ToolObservation).model_validate_json(encoded)
