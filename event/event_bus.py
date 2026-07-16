"""Thread-safe event bus with optional persistence.

When an S5 store reference is provided, every publish() also writes the
event to the events table. Consume/task_done semantics remain in-memory
for performance — the database write is fire-and-forget durability.
"""

import json
import logging
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Optional

from event.event_schema import Event

logger = logging.getLogger("eva.event_bus")


class EventBus:
    """Thread-safe event bus for publishing and consuming events."""

    _PERSIST_ALERT_THRESHOLD = 25  # warn after this many consecutive failures

    def __init__(self, s5_store=None) -> None:
        self._queue: Queue[Event] = Queue()
        self._s5 = s5_store
        self._publish_count = 0
        self._persist_count = 0
        self._persist_failures = 0
        self._consecutive_failures = 0
        self._persist_healthy = True
        self._alerted = False

    def publish(self, event: Event) -> None:
        """Publish an event to the bus and persist to S5 if configured."""
        if event is None:
            raise ValueError("Cannot publish None event")
        self._queue.put(event)

        # fire-and-forget persistence to S5 event trace
        if self._s5 is not None and hasattr(self._s5, "store"):
            try:
                self._s5.store.execute(
                    """INSERT OR IGNORE INTO events (id, type, source, timestamp, payload, correlation_id, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'captured')""",
                    (
                        event.id, event.type, event.source,
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps(event.payload, ensure_ascii=False),
                        event.correlation_id,
                    ),
                )
                self._publish_count += 1
                self._persist_count += 1
                self._consecutive_failures = 0
                if not self._persist_healthy:
                    logger.info("S5 persistence recovered after %d failures", self._persist_failures)
                    self._persist_healthy = True
                    self._alerted = False
            except Exception:
                self._publish_count += 1
                self._persist_failures += 1
                self._consecutive_failures += 1

                if self._consecutive_failures >= self._PERSIST_ALERT_THRESHOLD and not self._alerted:
                    self._persist_healthy = False
                    logger.error(
                        "S5 persistence degraded: %d/%d failures (last %d consecutive)",
                        self._persist_failures,
                        self._publish_count,
                        self._consecutive_failures,
                    )
                    self._alerted = True

    def consume(self, timeout: float = 0.5) -> Optional[Event]:
        """Consume an event from the bus with optional timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def task_done(self) -> None:
        """Mark a consumed event as processed."""
        self._queue.task_done()

    def size(self) -> int:
        """Get approximate queue size."""
        return self._queue.qsize()

    def drain(self) -> list[Event]:
        """Consume all pending events without blocking."""
        items: list[Event] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
                self._queue.task_done()
            except Empty:
                break
        return items

    def stats(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "published": self._publish_count,
            "persisted": self._persist_count,
            "persist_failures": self._persist_failures,
            "consecutive_failures": self._consecutive_failures,
            "persist_healthy": self._persist_healthy,
        }

