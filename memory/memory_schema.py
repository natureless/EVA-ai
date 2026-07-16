from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class MemoryType(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PERSONA = "persona"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    COMPRESSED = "compressed"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"
    CONFLICTED = "conflicted"


@dataclass
class MemoryRecord:
    id: str
    memory_type: MemoryType
    content: str
    source_event_id: Optional[str] = None
    salience: float = 0.0
    confidence: float = 0.5
    ttl_seconds: Optional[int] = None
    embedding_ref: Optional[str] = None
    summary_ref: Optional[str] = None
    conflict_keys: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: Optional[datetime] = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    def expires_at(self) -> Optional[datetime]:
        if self.ttl_seconds is None:
            return None
        return self.created_at + timedelta(seconds=self.ttl_seconds)
