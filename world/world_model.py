from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorldModel:
    focus: str = "idle"
    mode: str = "active"
    active_tasks: list[dict[str, Any]] = field(default_factory=list)
    recent_entities: list[str] = field(default_factory=list)
    last_reply: str = ""
    last_selected_agent: str = ""
    last_loop_id: str = ""
    last_loop_at: str | None = None
    last_user_message_at: str | None = None
    last_reminder_at: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldModel":
        return cls(
            focus=data.get("focus", "idle"),
            mode=data.get("mode", "active"),
            active_tasks=list(data.get("active_tasks", [])),
            recent_entities=list(data.get("recent_entities", [])),
            last_reply=data.get("last_reply", ""),
            last_selected_agent=data.get("last_selected_agent", ""),
            last_loop_id=data.get("last_loop_id", ""),
            last_loop_at=data.get("last_loop_at"),
            last_user_message_at=data.get("last_user_message_at"),
            last_reminder_at=data.get("last_reminder_at"),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )

    def apply_user_message(self, text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if text:
            self.focus = text[:80]
        self.last_user_message_at = now
        self.updated_at = now

    def apply_agent_result(self, *, reply: str, selected_agent: str, loop_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_reply = reply
        self.last_selected_agent = selected_agent
        self.last_loop_id = loop_id
        self.last_loop_at = now
        self.updated_at = now

    def apply_reminder(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_reminder_at = now
        self.updated_at = now
