"""Thread-safe event bus with optional persistence and back-pressure.

When an S5 store reference is provided, every publish() also writes the
event to the events table.  Consume/task_done semantics remain in-memory
for performance — the database write is fire-and-forget durability.

Back-pressure: the queue has a configurable maxsize (default 10 000).
When full, events are dropped per the configured overflow policy:
- "drop_oldest" (default): discard the oldest event to make room
- "drop_newest": reject the incoming event
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any, Literal, Optional

from event.event_schema import Event

logger = logging.getLogger("eva.event_bus")

DEFAULT_MAX_QUEUE_SIZE = 10_000
OverflowPolicy = Literal["drop_oldest", "drop_newest"]


class EventBus:
    """Thread-safe event bus with bounded queue and back-pressure.

    On overflow the oldest event is silently dropped (FIFO eviction).
    This prevents unbounded memory growth when the cognition loop
    cannot keep up with the publish rate.
    """

    _PERSIST_ALERT_THRESHOLD = 25  # warn after this many consecutive failures

    def __init__(
        self,
        s5_store: Any = None,
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        overflow_policy: OverflowPolicy = "drop_oldest",
    ) -> None:
        self._queue: Queue[Event] = Queue(maxsize=max_queue_size)
        self._max_queue_size = max_queue_size
        self._overflow_policy: OverflowPolicy = overflow_policy
        self._s5 = s5_store
        self._publish_count = 0
        self._persist_count = 0
        self._persist_failures = 0
        self._consecutive_failures = 0
        self._persist_healthy = True
        self._alerted = False
        self._dropped_count = 0

    def publish(self, event: Event) -> bool:
        """Publish an event to the bus and persist to S5 if configured.

        Returns True if the event was enqueued, False if it was dropped
        due to queue overflow.
        """
        if event is None:
            raise ValueError("Cannot publish None event")

        enqueued = self._enqueue(event)
        if not enqueued:
            self._dropped_count += 1
            logger.warning(
                "queue overflow (size=%d, max=%d) — event %s dropped (%d total dropped)",
                self._queue.qsize(), self._max_queue_size,
                event.type, self._dropped_count,
            )
            return False

        self._publish_count += 1

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

        return True

    def _enqueue(self, event: Event) -> bool:
        """Try to enqueue; apply overflow policy on full queue.

        Returns True if the event was enqueued, False if it was dropped.
        On drop_oldest, the oldest event is silently evicted (counted as
        dropped) and the new event is enqueued — returns True.
        """
        try:
            self._queue.put_nowait(event)
            return True
        except Exception:
            # Queue.Full — Python's queue module raises Full, not an Exception
            # subclass we can catch by name, so we catch broadly here.
            pass

        if self._overflow_policy == "drop_oldest":
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._dropped_count += 1  # evicted oldest counts as dropped
            except Empty:
                pass
            try:
                self._queue.put_nowait(event)
                return True
            except Exception:
                pass

        return False

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

    @property
    def dropped(self) -> int:
        """Number of events dropped due to queue overflow."""
        return self._dropped_count

    def stats(self) -> dict[str, Any]:
        return {
            "queue_size": self._queue.qsize(),
            "max_queue_size": self._max_queue_size,
            "published": self._publish_count,
            "persisted": self._persist_count,
            "persist_failures": self._persist_failures,
            "consecutive_failures": self._consecutive_failures,
            "persist_healthy": self._persist_healthy,
            "dropped": self._dropped_count,
            "overflow_policy": self._overflow_policy,
        }

