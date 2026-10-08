"""Bounded references to actual context inputs, separate from state authority."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def context_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise ValueError("context input exceeds evidence limit")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ViewReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["world_context_projection", "sqlite_s2_s3_recall"]


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tier: Literal["S2", "S3"]
    memory_id: str = Field(min_length=1, max_length=256)
    integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ActionContextEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    world: ViewReference | None = None
    memory: ViewReference | None = None
    memory_items: list[MemoryInput] = Field(default_factory=list, max_length=10)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent_kinds(self):
        if self.world is not None and self.world.kind != "world_context_projection":
            raise ValueError("invalid world reference kind")
        if self.memory is not None and self.memory.kind != "sqlite_s2_s3_recall":
            raise ValueError("invalid memory reference kind")
        identities = [(item.tier, item.memory_id) for item in self.memory_items]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate memory input identity")
        return self


def action_context_evidence(task) -> ActionContextEvidence:
    context = task.payload.get("context", {})
    refs = context.get("input_references", {})
    return ActionContextEvidence(
        world=refs.get("world"),
        memory=refs.get("memory"),
        memory_items=[
            MemoryInput(
                tier=row["tier"], memory_id=row["id"], integrity_hash=context_hash(row)
            )
            for row in context.get("memories", [])
        ],
        context_hash=context_hash(context),
        task_hash=context_hash({"kind": task.kind, "payload": task.payload}),
    )
