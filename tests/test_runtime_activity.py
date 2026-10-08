"""Actual callback lifetimes must outlive timeout receipts and stop requests."""

import threading
import time
from types import SimpleNamespace

from event.event_bus import EventBus
from runtime.agent_worker import worker_is_idle
from runtime.health import HealthService
from runtime.process_worker import ProcessAgentWorkerBackend
from runtime.scheduler import RuntimeScheduler


def test_process_backend_tracks_unisolated_helper_until_actual_completion():
    started, release = threading.Event(), threading.Event()
    backend = ProcessAgentWorkerBackend(max_workers=1)

    def slow_helper():
        started.set()
        assert release.wait(3)

    try:
        future = backend.submit(slow_helper)
        assert started.wait(1)
        assert future.cancel() is False
        assert worker_is_idle(backend) is False
        assert backend.stats["aux_pending"] == 1
        release.set()
        future.result(timeout=2)
        deadline = time.monotonic() + 1
        while not worker_is_idle(backend) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert worker_is_idle(backend) is True
        assert backend.stats["aux_pending"] == 0
    finally:
        release.set()
        backend.shutdown()


def test_scheduler_shutdown_waits_for_inflight_snapshot_and_can_retry():
    from apscheduler.events import EVENT_JOB_SUBMITTED

    entered, release = threading.Event(), threading.Event()
    submitted = threading.Event()

    def save_snapshot():
        entered.set()
        assert release.wait(3)

    scheduler = RuntimeScheduler(EventBus(), save_snapshot, {})
    scheduler._scheduler.add_listener(
        lambda event: submitted.set() if event.job_id == "test_snapshot" else None,
        EVENT_JOB_SUBMITTED,
    )
    scheduler.start()
    scheduler._scheduler.add_job(scheduler._run_tracked, args=(save_snapshot,), id="test_snapshot")
    try:
        assert entered.wait(1)
        # Wait until APScheduler has removed the one-shot registration. The
        # snapshot remains blocked, so shutdown must still report active work.
        assert submitted.wait(1)
        assert scheduler.shutdown(timeout=0) is False
        assert scheduler.system_state["scheduler_active_jobs"] == 1
        release.set()
        assert scheduler.shutdown(timeout=2) is True
        assert scheduler.system_state["scheduler_active_jobs"] == 0
    finally:
        release.set()
        scheduler.shutdown(timeout=2)


def test_readiness_detects_dead_consumer_despite_ready_startup_flag():
    health = HealthService({"ready": True}, loop=SimpleNamespace(is_running=False))
    assert health.ready()["status"] == "not_ready"


def test_unknown_execution_capacity_is_not_reported_idle():
    assert worker_is_idle(SimpleNamespace(stats={"backend": "unknown"})) is False
