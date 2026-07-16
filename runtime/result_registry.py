import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingResult:
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)


class ResultRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingResult] = {}

    def create(self, correlation_id: str) -> None:
        with self._lock:
            self._pending[correlation_id] = PendingResult()

    def fulfill(self, correlation_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            item = self._pending.get(correlation_id)
            if item is None:
                return
            item.payload = payload
            item.event.set()

    def wait(self, correlation_id: str, timeout: float) -> dict[str, Any] | None:
        with self._lock:
            item = self._pending.get(correlation_id)
            if item is None:
                return None

        ok = item.event.wait(timeout=timeout)
        if not ok:
            return None

        with self._lock:
            item = self._pending.get(correlation_id)
            return item.payload if item else None

    def pop(self, correlation_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._pending.pop(correlation_id, None)
            return item.payload if item else None

    def cleanup(self, ttl_sec: float = 60.0) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            expired = [
                key for key, item in self._pending.items()
                if now - item.created_at > ttl_sec
            ]
            for key in expired:
                self._pending.pop(key, None)
                removed += 1
        return removed

    def size(self) -> int:
        with self._lock:
            return len(self._pending)
