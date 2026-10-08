"""Optional request persistence contract at the shared registry boundary."""

from typing import Any, Protocol

from event.event_schema import Event


class ActionContextError(RuntimeError):
    """Fail before invocation or preserve uncertainty after a receipt write failure."""


class RequestAdmissionError(RuntimeError):
    def __init__(self, reason: str, *, task_id: str = "", event_id: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.task_id = task_id
        self.event_id = event_id


class RequestPersistence(Protocol):
    def reserve(
        self, event: Event, *, timeout_sec: float, retention_sec: float
    ) -> dict[str, Any]: ...

    def claim(self, event: Event) -> str: ...

    def record_receipt(self, receipt: dict[str, Any]) -> None: ...

    def end_processing(
        self, task_id: str, event_id: str, *, returned_ok: bool | None
    ) -> None: ...

    def lookup(self, task_id: str) -> dict[str, Any]: ...

    def resume_details(self, event: Event) -> dict[str, float] | None: ...

    def has_request(self, task_id: str) -> bool: ...

    def begin_agent_action(
        self, event: Event, agent: str, evidence: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def finish_agent_action(
        self, event: Event, binding: dict[str, Any] | None, *, returned_ok: bool | None
    ) -> None: ...
