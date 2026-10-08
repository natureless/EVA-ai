from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from event.contracts import EventMetadata


EventType = Literal["user_message", "system_tick", "reminder_trigger", "maintenance", "github_push", "github_pr", "github_issue", "github_workflow"]


class Event(EventMetadata):
    """Stable consumer facade over the v1 event metadata contract."""
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=256)
    type: EventType


class TraceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    loop_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    decision: str
    agent: str
    result_summary: str
    duration_ms: int
