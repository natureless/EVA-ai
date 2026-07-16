from queue import Empty, Queue
from typing import Optional

from event.event_schema import Event


class EventBus:
    """Thread-safe event bus for publishing and consuming events."""
    
    def __init__(self) -> None:
        self._queue: Queue[Event] = Queue()

    def publish(self, event: Event) -> None:
        """Publish an event to the bus."""
        if event is None:
            raise ValueError("Cannot publish None event")
        self._queue.put(event)

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

