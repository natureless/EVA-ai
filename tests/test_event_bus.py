"""Unit tests for EventBus back-pressure, overflow, and drain."""

import pytest

from event.event_bus import EventBus, DEFAULT_MAX_QUEUE_SIZE
from event.event_schema import Event

# Use valid event types from EventType Literal
_VALID_TYPE = "system_tick"


def _event(**kwargs) -> Event:
    defaults = {"type": _VALID_TYPE, "source": "test", "payload": {}}
    return Event(**(defaults | kwargs))


class TestEventBusBasic:
    def test_publish_and_consume(self):
        bus = EventBus(max_queue_size=10)
        event = _event()
        assert bus.publish(event) is True
        assert bus.size() == 1

        consumed = bus.consume(timeout=0.1)
        assert consumed is not None
        assert consumed.id == event.id
        bus.task_done()
        assert bus.size() == 0

    def test_consume_empty_returns_none(self):
        bus = EventBus(max_queue_size=10)
        assert bus.consume(timeout=0.05) is None

    def test_publish_none_raises(self):
        bus = EventBus(max_queue_size=10)
        with pytest.raises(ValueError, match="None event"):
            bus.publish(None)  # type: ignore

    def test_fifo_ordering(self):
        bus = EventBus(max_queue_size=100)
        events = [_event(payload={"n": i}) for i in range(5)]
        for e in events:
            bus.publish(e)

        for i in range(5):
            consumed = bus.consume(timeout=0.1)
            assert consumed is not None
            assert consumed.payload["n"] == i
            bus.task_done()


class TestEventBusOverflow:
    def test_drop_oldest_policy(self):
        """When queue is full, drop_oldest evicts oldest, counts as dropped."""
        bus = EventBus(max_queue_size=3, overflow_policy="drop_oldest")

        e1 = _event()
        e2 = _event()
        e3 = _event()
        e4 = _event()

        bus.publish(e1)
        bus.publish(e2)
        bus.publish(e3)  # queue full [e1, e2, e3]

        # e4 triggers overflow → oldest (e1) evicted
        assert bus.publish(e4) is True
        assert bus.dropped == 1  # evicted oldest counted as dropped
        assert bus.size() == 3

        # e2 should be first (e1 was evicted)
        c1 = bus.consume(timeout=0.1)
        assert c1 is not None and c1.id == e2.id
        bus.task_done()

    def test_drop_newest_policy(self):
        """When queue is full, drop_newest rejects the incoming event."""
        bus = EventBus(max_queue_size=3, overflow_policy="drop_newest")

        for _ in range(3):
            bus.publish(_event())
        assert bus.size() == 3

        e4 = _event()
        assert bus.publish(e4) is False
        assert bus.dropped == 1
        assert bus.size() == 3  # queue unchanged

    def test_dropped_counter_accumulates(self):
        bus = EventBus(max_queue_size=2, overflow_policy="drop_newest")

        for _ in range(2):
            bus.publish(_event())

        # Try to publish 5 more — all should be dropped
        for _ in range(5):
            bus.publish(_event())

        assert bus.dropped == 5

    def test_queue_size_never_exceeds_max(self):
        bus = EventBus(max_queue_size=5, overflow_policy="drop_oldest")
        for i in range(20):
            bus.publish(_event())
        assert bus.size() <= 5


class TestEventBusDrain:
    def test_drain_returns_all_events(self):
        bus = EventBus(max_queue_size=10)
        for i in range(5):
            bus.publish(_event())

        drained = bus.drain()
        assert len(drained) == 5
        assert bus.size() == 0

    def test_drain_empty_queue(self):
        bus = EventBus(max_queue_size=10)
        drained = bus.drain()
        assert drained == []


class TestEventBusStats:
    def test_stats_include_all_fields(self):
        bus = EventBus(max_queue_size=100, overflow_policy="drop_newest")
        bus.publish(_event())

        s = bus.stats()
        assert s["queue_size"] == 1
        assert s["max_queue_size"] == 100
        assert s["overflow_policy"] == "drop_newest"
        assert s["dropped"] == 0
        assert s["published"] == 1

    def test_dropped_appears_in_stats(self):
        bus = EventBus(max_queue_size=1, overflow_policy="drop_newest")
        bus.publish(_event())
        bus.publish(_event())  # dropped

        s = bus.stats()
        assert s["dropped"] == 1
        assert s["published"] == 1  # only the first was published


class TestEventBusDefault:
    def test_default_max_queue_size(self):
        bus = EventBus()
        assert bus._max_queue_size == DEFAULT_MAX_QUEUE_SIZE

    def test_default_policy_is_drop_oldest(self):
        bus = EventBus(max_queue_size=2)
        assert bus._overflow_policy == "drop_oldest"
        for _ in range(3):
            bus.publish(_event())
        # Oldest dropped, latest enqueued
        assert bus.dropped == 1
        assert bus.size() == 2
