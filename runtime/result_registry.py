"""Bounded in-process request receipts with deadlines and terminal retention.

Registry access enforces deadlines even if the consumer has failed. Optional
request persistence protects actual HTTP admission/claims and restores retained
terminal receipts. The default registry is process-local.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import math
import logging
import threading
import time
from typing import Any, Callable

from event.event_schema import Event
from runtime.request_persistence import (
    ActionContextError,
    RequestAdmissionError,
    RequestPersistence,
)
from memory.context_evidence import action_context_evidence


@dataclass
class PendingResult:
    created_at: float
    deadline: float
    event_id: str = ""
    task_id: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict[str, Any] | None = None
    started: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    durable: bool = False
    retention_sec: float | None = None


class ResultRegistry:
    def __init__(
        self,
        *,
        retention_sec: float = 60.0,
        pending_timeout_sec: float = 300.0,
        max_entries: int = 20_000,
        clock: Callable[[], float] = time.monotonic,
        receipt_observer: Callable[[dict[str, Any]], None] | None = None,
        persistence: RequestPersistence | None = None,
    ) -> None:
        for value in (retention_sec, pending_timeout_sec):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("receipt timeouts must be finite and positive")
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self._lock = threading.Lock()
        self._pending: dict[str, PendingResult] = {}
        self._clock = clock
        self.retention_sec = retention_sec
        self.pending_timeout_sec = pending_timeout_sec
        self.max_entries = max_entries
        self._last_sweep = float("-inf")
        self._expired = 0
        self._capacity_rejections = 0
        self._receipt_observer = receipt_observer
        self._receipt_observer_errors = 0
        self._persistence = persistence
        self._persistence_errors = 0

    def create(
        self, correlation_id: str, *, event_id: str = "", event: Event | None = None
    ) -> bool:
        """Reserve before publishing. Never replace a waiter or retained result."""
        with self._lock:
            now = self._clock()
            self._periodic_sweep(now)
            if correlation_id in self._pending:
                return False
            if len(self._pending) >= self.max_entries:
                self._capacity_rejections += 1
                return False
            durable = self._persistence is not None and event is not None
            if durable:
                assert event is not None and self._persistence is not None
                if event.correlation_id != correlation_id or event.id != event_id:
                    raise RequestAdmissionError("durable_request_unavailable")
                try:
                    admission = self._persistence.reserve(
                        event,
                        timeout_sec=self.pending_timeout_sec,
                        retention_sec=self.retention_sec,
                    )
                except Exception:
                    self._persistence_errors += 1
                    raise RequestAdmissionError("durable_request_unavailable") from None
                if admission.get("reason"):
                    raise RequestAdmissionError(
                        admission["reason"],
                        task_id=admission.get("task_id", ""),
                        event_id=admission.get("event_id", ""),
                    )
            self._pending[correlation_id] = PendingResult(
                created_at=now,
                deadline=now + self.pending_timeout_sec,
                event_id=event_id,
                task_id=correlation_id,
                durable=durable,
                retention_sec=self.retention_sec,
            )
            return True

    def restore_pending(self, event: Event) -> str:
        """Hydrate proven startup admission without renewing its deadline."""
        with self._lock:
            if self._persistence is None or not event.correlation_id:
                return "unavailable"
            now = self._clock()
            self._periodic_sweep(now)
            item = self._get(event.correlation_id, now)
            if item is not None:
                if item.event_id != event.id:
                    return "event_mismatch"
                if not item.durable:
                    raise ValueError(
                        "request recovery conflicts with volatile registration"
                    )
                return (
                    "terminal"
                    if item.payload is not None
                    else "running"
                    if item.started
                    else "pending"
                )
            details = self._persistence.resume_details(event)
            if details is None:
                # Persistent reads also enforce the original UTC deadline.
                view = self._persistence.lookup(event.correlation_id)
                return "terminal" if view["state"] == "terminal" else "unavailable"
            if len(self._pending) >= self.max_entries:
                return "capacity_full"
            for value in details.values():
                if isinstance(value, bool) or not math.isfinite(value):
                    raise ValueError("invalid durable resume timing")
            if (
                details["age_sec"] < 0
                or details["remaining_sec"] <= 0
                or details["retention_sec"] <= 0
            ):
                raise ValueError("invalid durable resume lifetime")
            # Read monotonic time after I/O. SQLite claim checks UTC again before effects.
            now = self._clock()
            self._pending[event.correlation_id] = PendingResult(
                created_at=now - details["age_sec"],
                deadline=now + details["remaining_sec"],
                event_id=event.id,
                task_id=event.correlation_id,
                durable=True,
                retention_sec=details["retention_sec"],
            )
            return "pending"

    def begin(
        self, correlation_id: str, *, event_id: str = "", event: Event | None = None
    ) -> str:
        """Claim execution once, before any model, memory or tool side effect."""
        with self._lock:
            now = self._clock()
            item = self._get(correlation_id, now)
            if item is None:
                return "missing"
            if item.event_id and event_id and item.event_id != event_id:
                return "event_mismatch"
            if item.payload is not None:
                return "terminal"
            if item.started:
                return "running"
            if self._persistence is not None and not item.durable:
                try:
                    if self._persistence.has_request(correlation_id):
                        return "durable_registration_conflict"
                except Exception:
                    self._persistence_errors += 1
                    self._complete(
                        item,
                        self._failure(
                            correlation_id, item, "request_claim_unavailable"
                        ),
                        now,
                    )
                    return "terminal"
            if item.durable:
                assert self._persistence is not None
                if (
                    event is None
                    or event.correlation_id != correlation_id
                    or event.id != item.event_id
                ):
                    return "event_mismatch"
                try:
                    claimed = self._persistence.claim(event)
                    if claimed == "terminal":
                        view = self._persistence.lookup(correlation_id)
                        if view["state"] == "terminal":
                            self._complete(item, view["payload"], now)
                        return "terminal"
                    if claimed != "started":
                        return claimed
                except Exception:
                    self._persistence_errors += 1
                    self._complete(
                        item,
                        self._failure(
                            correlation_id, item, "request_claim_unavailable"
                        ),
                        now,
                    )
                    return "terminal"
            item.started = True
            item.started_at = now
            return "started"

    def fulfill(self, correlation_id: str, payload: dict[str, Any]) -> bool:
        """The first terminal wins. Late results cannot rewrite timeout receipts."""
        with self._lock:
            now = self._clock()
            item = self._get(correlation_id, now)
            if item is None or item.payload is not None:
                return False
            self._complete(item, payload, now)
            return True

    def begin_agent_action(self, event: Event, agent: str, task):
        with self._lock:
            if (
                self._persistence is None
                or getattr(self._persistence, "enable_action_context", False)
                is not True
            ):
                return None
            if not event.correlation_id:
                return None
            item = self._get(event.correlation_id, self._clock())
            if item is None or not item.durable:
                try:
                    if event.correlation_id and self._persistence.has_request(
                        event.correlation_id
                    ):
                        raise ActionContextError(
                            "agent context requires a live request"
                        )
                except Exception:
                    raise ActionContextError(
                        "agent context requires a live request"
                    ) from None
                return None  # Internal events have no durable HTTP claim.
            if (
                item.event_id != event.id
                or not item.started
                or item.payload is not None
            ):
                raise ActionContextError("agent context requires a live request")
            try:
                evidence = action_context_evidence(task).model_dump(mode="json")
                return self._persistence.begin_agent_action(event, agent, evidence)
            except Exception:
                self._persistence_errors += 1
                raise ActionContextError("agent context commit unavailable") from None

    def finish_agent_action(self, event: Event, binding, *, returned_ok):
        if binding is None:
            return
        with self._lock:
            assert self._persistence is not None
            try:
                self._persistence.finish_agent_action(
                    event, binding, returned_ok=returned_ok
                )
            except Exception:
                self._persistence_errors += 1
                raise ActionContextError(
                    "agent observation commit unavailable"
                ) from None

    def _complete(
        self, item: PendingResult, payload: dict[str, Any], now: float
    ) -> None:
        item.payload = deepcopy(payload)
        item.completed_at = now
        self._persist(item)
        if self._receipt_observer is not None:
            try:
                # The observer must not call back into this registry. IDs come
                # from admission, not from agent-controlled result metadata.
                self._receipt_observer(
                    {
                        **deepcopy(payload),
                        "task_id": item.task_id,
                        "event_id": item.event_id,
                    }
                )
            except Exception:
                self._receipt_observer_errors += 1
                logging.getLogger("eva.results").exception(
                    "receipt observer failed; canonical receipt retained"
                )
        item.event.set()

    def _persist(self, item: PendingResult) -> None:
        if item.durable and item.payload is not None:
            assert self._persistence is not None
            try:
                self._persistence.record_receipt(
                    {
                        **deepcopy(item.payload),
                        "task_id": item.task_id,
                        "event_id": item.event_id,
                    }
                )
            except Exception:
                self._persistence_errors += 1
                logging.getLogger("eva.results").error(
                    "durable receipt persistence failed; canonical memory receipt retained"
                )

    def end_processing(
        self, correlation_id: str, *, event_id: str, returned_ok: bool | None
    ) -> None:
        """Observe handler return and retry receipt writes, never execution."""
        with self._lock:
            item = self._pending.get(correlation_id)
            if self._persistence is not None:
                if item is not None and item.durable:
                    self._persist(item)
                try:
                    self._persistence.end_processing(
                        correlation_id, event_id, returned_ok=returned_ok
                    )
                except Exception:
                    self._persistence_errors += 1
                    logging.getLogger("eva.results").error(
                        "durable handler observation failed; claim retained"
                    )

    def reject_unpublished(self, correlation_id: str, reason: str) -> None:
        """Seal a durable reservation if publishing fails, then release RAM."""
        with self._lock:
            item = self._pending.get(correlation_id)
            if (
                item is not None
                and item.durable
                and item.payload is None
                and not item.started
            ):
                self._complete(
                    item, self._failure(correlation_id, item, reason), self._clock()
                )
            self._pending.pop(correlation_id, None)

    @staticmethod
    def _failure(
        correlation_id: str, item: PendingResult, reason: str
    ) -> dict[str, Any]:
        return {
            "task_id": correlation_id,
            "event_id": item.event_id,
            "ok": False,
            "error": reason,
            "terminal_state": "outcome_unknown"
            if item.started
            else ("expired" if reason == "request_deadline_exceeded" else "rejected"),
            "execution_state": "may_still_be_running"
            if item.started
            else "not_started",
            "reply": "请求已超过等待期限，执行结果尚不确定。"
            if item.started
            else f"本次请求未执行：{reason}。",
            "selected_agent": "system",
            "duration_ms": 0,
            "review": {
                "status": "not_assessed",
                "passed": None,
                "fact_verified": False,
            },
        }

    def _expire(self, key: str, item: PendingResult, now: float) -> bool:
        if item.payload is None and now >= item.deadline:
            self._complete(
                item, self._failure(key, item, "request_deadline_exceeded"), now
            )
            self._expired += 1
            return True
        return False

    def _get(self, key: str, now: float) -> PendingResult | None:
        item = self._pending.get(key)
        if item is not None:
            self._expire(key, item, now)
            if item.completed_at is not None and now - item.completed_at >= (
                item.retention_sec or self.retention_sec
            ):
                del self._pending[key]
                return None
        return item

    def lookup(self, correlation_id: str) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            item = self._get(correlation_id, now)
            if item is None:
                if self._persistence is not None:
                    return self._persistence.lookup(correlation_id)
                return {"state": "missing", "payload": None}
            return {
                "state": "terminal"
                if item.payload is not None
                else "running"
                if item.started
                else "pending",
                "event_id": item.event_id,
                "payload": deepcopy(item.payload),
                "timing": {
                    "admission_to_execution_ms": None
                    if item.started_at is None
                    else (item.started_at - item.created_at) * 1000,
                    "admission_to_terminal_ms": None
                    if item.completed_at is None
                    else (item.completed_at - item.created_at) * 1000,
                    "waiting_age_ms": (now - item.created_at) * 1000
                    if not item.started and item.payload is None
                    else None,
                },
                "expires_in_sec": max(
                    0.0,
                    (
                        item.completed_at + (item.retention_sec or self.retention_sec)
                        if item.completed_at is not None
                        else item.deadline
                    )
                    - now,
                ),
            }

    def wait(self, correlation_id: str, timeout: float) -> dict[str, Any] | None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("wait timeout must be finite and nonnegative")
        wait_deadline = time.perf_counter() + timeout
        with self._lock:
            now = self._clock()
            item = self._get(correlation_id, now)
            if item is None:
                return None
        while True:
            with self._lock:
                now = self._clock()
                self._expire(correlation_id, item, now)
                remaining = max(0.0, wait_deadline - time.perf_counter())
                # Keep the waiter object even if an explicit pop removed its key.
                if item.payload is not None or remaining <= 0:
                    return deepcopy(item.payload)
                wait_sec = min(remaining, max(0.0, item.deadline - now))
            # A wait can finish before a coarse deadline clock advances (Windows).
            # Recheck both deadlines rather than returning a premature empty receipt.
            item.event.wait(timeout=wait_sec)

    def reject_waiting(self, reason: str = "runtime_stopping") -> list[dict[str, Any]]:
        """Close queued requests immediately; active work keeps its real lifetime."""
        with self._lock:
            now = self._clock()
            receipts = []
            for key, item in self._pending.items():
                if item.payload is None and not item.started:
                    payload = self._failure(key, item, reason)
                    self._complete(item, payload, now)
                    receipts.append(deepcopy(payload))
            return receipts

    def pop(self, correlation_id: str) -> dict[str, Any] | None:
        """Explicit removal for rejected admission/admin callers, not HTTP reads."""
        with self._lock:
            item = self._pending.pop(correlation_id, None)
            return deepcopy(item.payload) if item else None

    def peek(self, correlation_id: str) -> dict[str, Any] | None:
        return self.lookup(correlation_id)["payload"]

    def _sweep(self, now: float, ttl_sec: float | None) -> int:
        removed = 0
        for key, item in list(self._pending.items()):
            if self._expire(key, item, now):
                continue  # A newly expired request gets a full receipt window.
            ttl = (
                ttl_sec
                if ttl_sec is not None
                else (item.retention_sec or self.retention_sec)
            )
            if item.completed_at is not None and now - item.completed_at >= ttl:
                del self._pending[key]
                removed += 1
        self._last_sweep = now
        return removed

    def _periodic_sweep(self, now: float) -> None:
        if now - self._last_sweep >= 1:
            self._sweep(now, None)

    def cleanup(self, ttl_sec: float | None = None) -> int:
        ttl = ttl_sec
        if ttl is not None and (
            isinstance(ttl, bool) or not math.isfinite(ttl) or ttl < 0
        ):
            raise ValueError("retention must be finite and nonnegative")
        with self._lock:
            return self._sweep(self._clock(), ttl)

    def size(self) -> int:
        with self._lock:
            self._periodic_sweep(self._clock())
            return len(self._pending)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._periodic_sweep(self._clock())
            terminal = sum(item.payload is not None for item in self._pending.values())
            return {
                "entries": len(self._pending),
                "terminal": terminal,
                "pending": len(self._pending) - terminal,
                "capacity": self.max_entries,
                "deadline_expirations": self._expired,
                "capacity_rejections": self._capacity_rejections,
                "receipt_observer_errors": self._receipt_observer_errors,
                "durable_requests_attached": self._persistence is not None,
                "request_persistence_errors": self._persistence_errors,
            }
