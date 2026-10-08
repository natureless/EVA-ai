"""Thread-safe event bus with optional persistence and back-pressure.

When an S5 store reference is provided, admitted events are also written to
the events table. Consume/task_done semantics remain in-memory; persistence
is synchronous and best effort, with failures exposed in bus statistics.

Back-pressure: the queue has a configurable maxsize (default 10 000).
When full, events are dropped per the configured overflow policy:
- "drop_oldest" (default): discard the oldest uncorrelated background event
- "drop_newest": reject the incoming event
"""

from __future__ import annotations

import json
import logging
import threading
from queue import Empty, Queue
from typing import Any, Literal, Optional

from event.event_schema import Event
from event.codec import encode_event

logger = logging.getLogger("eva.event_bus")

DEFAULT_MAX_QUEUE_SIZE = 10_000
OverflowPolicy = Literal["drop_oldest", "drop_newest"]


class _EventQueue(Queue):
    def offer(self, event: Event, *, evict_background: bool) -> tuple[bool, bool]:
        """Atomic capacity decision, preserving FIFO order of surviving work."""
        with self.not_full:
            evicted = False
            if self.maxsize > 0 and self._qsize() >= self.maxsize:
                if not evict_background:
                    return False, False
                for index, queued in enumerate(self.queue):
                    if queued.type in {"system_tick", "maintenance"} and not queued.correlation_id:
                        del self.queue[index]
                        self.unfinished_tasks -= 1
                        evicted = True
                        break
                if not evicted:
                    return False, False
            self._put(event)
            self.unfinished_tasks += 1
            self.not_empty.notify()
            return True, evicted


class EventBus:
    """Thread-safe event bus with bounded queue and back-pressure.

    User messages and correlated requests cannot be evicted. On overflow,
    drop_oldest may replace uncorrelated background work; otherwise admission
    is rejected. Surviving events retain FIFO order.
    """

    _PERSIST_ALERT_THRESHOLD = 25  # warn after this many consecutive failures

    def __init__(
        self,
        s5_store: Any = None,
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        overflow_policy: OverflowPolicy = "drop_oldest",
    ) -> None:
        if type(max_queue_size) is not int or max_queue_size < 1:
            raise ValueError("max_queue_size must be a positive integer")
        if overflow_policy not in {"drop_oldest", "drop_newest"}:
            raise ValueError("unsupported overflow policy")
        self._queue: _EventQueue = _EventQueue(maxsize=max_queue_size)
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
        self._evicted_background = 0
        self._rejected_full = 0
        self._admission_lock = threading.RLock()
        self._closed = False
        self._rejected_closed = 0

    @property
    def accepting(self) -> bool:
        """Whether new events can enter this bus (queue capacity is separate)."""
        with self._admission_lock:
            return not self._closed

    def close(self) -> None:
        """Permanently close admission without discarding pending events.

        The lock also covers persistence. Once this returns, no publisher can
        begin or continue an S5 write through this bus. A write already in
        progress completes before close returns; consumption remains available.
        """
        with self._admission_lock:
            self._closed = True

    def publish(self, event: Event) -> bool:
        """Publish an event to the bus and persist to S5 if configured.

        Returns True if admitted, False when closed or at queue capacity.
        Closing and publishing share a lock so storage can safely be released
        after admission has closed and consumers have finished.
        """
        if event is None:
            raise ValueError("Cannot publish None event")
        # Revalidate even model_construct/model_copy inputs before queue or store effects.
        # Transfer a private snapshot, not the producer's mutable object graph.
        event = Event.model_validate(event.model_dump())
        contract = encode_event(event)
        persisted = (
            event.id, event.type, event.source, event.timestamp.isoformat(),
            json.dumps(event.payload, ensure_ascii=False, allow_nan=False), event.correlation_id, contract,
        )

        with self._admission_lock:
            if self._closed:
                self._rejected_closed += 1
                return False
            return self._publish_admitted(event, persisted)

    def _publish_admitted(self, event: Event, persisted: tuple[Any, ...]) -> bool:
        """Enqueue and persist while holding the admission lock."""
        enqueued = self._enqueue(event)
        if not enqueued:
            self._dropped_count += 1
            self._rejected_full += 1
            logger.warning(
                "queue overflow (size=%d, max=%d) — event %s dropped (%d total dropped)",
                self._queue.qsize(), self._max_queue_size,
                event.type, self._dropped_count,
            )
            return False

        self._publish_count += 1

        # Synchronous best-effort persistence to S5 event trace.
        if self._s5 is not None and hasattr(self._s5, "store"):
            try:
                self._s5.store.execute(
                    """INSERT OR IGNORE INTO events (id, type, source, timestamp, payload, correlation_id, status, event_contract)
                       VALUES (?, ?, ?, ?, ?, ?, 'captured', ?)""",
                    persisted,
                )
                self._persist_count += 1
                self._consecutive_failures = 0
                if not self._persist_healthy:
                    logger.info("S5 persistence recovered after %d failures", self._persist_failures)
                    self._persist_healthy = True
                    self._alerted = False
            except Exception:
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
        admitted, evicted = self._queue.offer(
            event, evict_background=self._overflow_policy == "drop_oldest",
        )
        if evicted:
            self._evicted_background += 1
            self._dropped_count += 1
        return admitted

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
        with self._admission_lock:
            return {
                "queue_size": self._queue.qsize(),
                "max_queue_size": self._max_queue_size,
                "published": self._publish_count,
                "persisted": self._persist_count,
                "persist_failures": self._persist_failures,
                "consecutive_failures": self._consecutive_failures,
                "persist_healthy": self._persist_healthy,
                "dropped": self._dropped_count,
                "evicted_background": self._evicted_background,
                "rejected_full": self._rejected_full,
                "overflow_policy": self._overflow_policy,
                "closed": self._closed,
                "accepting": not self._closed,
                "rejected_closed": self._rejected_closed,
            }
