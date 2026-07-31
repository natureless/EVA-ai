"""Unit tests for RuntimeScheduler."""

from unittest.mock import MagicMock

import pytest

from event.event_bus import EventBus
from runtime.scheduler import RuntimeScheduler


class TestScheduler:
    def test_start_is_idempotent(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched.start()
        first_started = sched._started
        sched.start()  # second call should be no-op
        assert sched._started == first_started
        sched.shutdown()

    def test_start_sets_state_flag(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched.start()
        assert state["scheduler_running"] is True
        sched.shutdown()

    def test_shutdown_clears_state_flag(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched.start()
        sched.shutdown()
        assert state["scheduler_running"] is False
        assert sched._started is False

    def test_shutdown_when_not_started_is_noop(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        # Should not raise
        sched.shutdown()
        assert sched._started is False

    def test_list_jobs_returns_job_ids(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched.start()
        jobs = sched.list_jobs()
        assert len(jobs) == 3
        job_ids = {j["id"] for j in jobs}
        assert job_ids == {"system_tick_job", "maintenance_job", "snapshot_job"}
        sched.shutdown()

    def test_publish_system_tick(self):
        bus = MagicMock(spec=EventBus)
        bus.size.return_value = 0
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched._publish_system_tick()
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.type == "system_tick"
        assert event.source == "scheduler"
        assert "scheduled_at" in event.payload
        assert state["pending_events"] == 0

    def test_publish_maintenance(self):
        bus = MagicMock(spec=EventBus)
        bus.size.return_value = 3
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched._publish_maintenance()
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.type == "maintenance"
        assert event.source == "scheduler"
        assert event.payload["kind"] == "periodic_maintenance"
        assert state["pending_events"] == 3

    def test_save_snapshot_calls_function(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(bus, snap, state)
        sched._save_snapshot()
        snap.assert_called_once()

    def test_custom_intervals_passed_to_jobs(self):
        bus = MagicMock(spec=EventBus)
        state: dict = {}
        snap = MagicMock()
        sched = RuntimeScheduler(
            bus, snap, state,
            tick_interval_sec=5,
            maintenance_interval_sec=30,
            snapshot_interval_sec=90,
        )
        sched.start()
        jobs = sched.list_jobs()
        assert len(jobs) == 3
        sched.shutdown()
