from datetime import datetime, timezone
from typing import Any, Dict, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


EventType = Literal["user_message", "system_tick", "agent_result", "reminder_trigger", "maintenance"]


class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    source: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    status: str = "pending"


class TraceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    loop_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    decision: str
    agent: str
    result_summary: str
    duration_ms: int
