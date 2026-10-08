from concurrent.futures import Executor, Future
import threading

import pytest

from event.event_bus import EventBus
from event.event_schema import Event
from packages.minimal_brain.kernel import KernelConfig, MinimalBrainKernel


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ControlledExecutor(Executor):
    """Explicit completions make queue and shutdown races reproducible."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_running_or_notify_cancel()
        self.jobs.append((future, fn, args, kwargs))
        return future

    def complete(self, index=0, *, error=None):
        future, fn, args, kwargs = self.jobs[index]
        if error:
            future.set_exception(error)
        else:
            future.set_result(fn(*args, **kwargs))


def message(text="hello", **kwargs):
    return Event(type="user_message", source="test", payload={"text": text}, **kwargs)


def receipt(event, context):
    return {"ok": True, "reply": event.payload.get("text", event.type), "selected_agent": "test"}


@pytest.fixture
def rig():
    clock, executor, bus = Clock(), ControlledExecutor(), EventBus()
    rejected = []
    kernel = MinimalBrainKernel(
        bus, receipt, executor=executor, clock=clock,
        on_reject=lambda event, reason: rejected.append((event.id, reason)),
        config=KernelConfig(queue_capacity=4, max_wait_sec=5),
    )
    yield kernel, bus, clock, executor, rejected
    for future, _, _, _ in executor.jobs:
        if not future.done():
            future.set_result({"ok": False, "reply": "teardown"})
    kernel.stop(timeout=0)


def test_blocked_cognition_does_not_block_multirate_nodes():
    started, release = threading.Event(), threading.Event()
    clock, bus = Clock(), EventBus()

    def blocking(event, context):
        started.set()
        assert release.wait(3)
        return receipt(event, context)

    kernel = MinimalBrainKernel(bus, blocking, clock=clock)
    try:
        bus.publish(message())
        kernel.step()
        assert started.wait(1)
        for _ in range(5):
            clock.advance(0.05)
            kernel.step()
        ticks = kernel.stats["node_ticks"]
        assert ticks["body"] == 6
        assert ticks["attention"] >= 3
        assert ticks["goals"] == 2
        assert kernel.stats["processor_busy"] is True
        assert ticks["feedback"] == 0
    finally:
        release.set()
        assert kernel.stop(timeout=2)


@pytest.mark.parametrize("pressure, expected", [(0.0, "user_message"), (0.95, "maintenance")])
def test_measured_pressure_changes_actual_dispatch_order(pressure, expected):
    clock, executor, bus = Clock(), ControlledExecutor(), EventBus()
    kernel = MinimalBrainKernel(
        bus, receipt, clock=clock, executor=executor,
        resource_probe=lambda: {"pressure": pressure},
    )
    try:
        bus.publish(message("occupy processor"))
        kernel.step()
        bus.publish(Event(type="maintenance", source="scheduler"))
        bus.publish(message("next user task"))
        for _ in range(6):
            clock.advance(0.05)
            kernel.step()
        assert kernel.snapshot()["workspace"][0]["event_type"] == expected
        executor.complete()
        kernel.step()
        assert executor.jobs[1][2][0].type == expected
        assert executor.jobs[1][2][1]["recent_feedback"][0]["summary"] == "occupy processor"
    finally:
        for future, _, _, _ in executor.jobs:
            if not future.done():
                future.set_result({"ok": True, "reply": "cleanup"})
        kernel.stop(timeout=0)


def test_duplicate_delivery_does_not_reexecute_or_duplicate_feedback(rig):
    kernel, bus, _, executor, _ = rig
    event = message()
    bus.publish(event)
    bus.publish(event)
    kernel.step()
    assert len(executor.jobs) == 1
    executor.complete()
    bus.publish(event)
    kernel.step()
    assert len(executor.jobs) == 1
    assert kernel.stats["duplicates"] == 2
    assert kernel.stats["completed"] == 1
    assert len(kernel.snapshot()["memory"]) == 1
    assert bus.size() == 0


def test_capacity_rejection_and_queue_only_cancellation(rig):
    kernel, bus, _, executor, rejected = rig
    active = message("active")
    bus.publish(active)
    kernel.step()
    queued = [message(str(index)) for index in range(5)]
    for event in queued:
        bus.publish(event)
    kernel.step()
    assert (queued[-1].id, "cognitive_queue_full") in rejected
    assert kernel.cancel(active.id) is False
    assert kernel.cancel(queued[0].id) is True
    assert (queued[0].id, "cancelled") in rejected
    executor.complete()
    kernel.step()
    assert executor.jobs[1][2][0].id != queued[0].id


def test_expired_queued_goal_never_dispatches(rig):
    kernel, bus, clock, executor, rejected = rig
    bus.publish(message("active"))
    kernel.step()
    expired = message("will expire")
    bus.publish(expired)
    kernel.step()
    clock.advance(5.0)
    kernel.step()
    executor.complete()
    kernel.step()
    assert len(executor.jobs) == 1
    assert (expired.id, "cognitive_queue_timeout") in rejected
    goals = {g["event_id"]: g for g in kernel.snapshot()["goals"]}
    assert goals[expired.id]["status"] == "expired"


def test_failed_processor_releases_completed_slot_and_records_failure(rig):
    kernel, bus, _, executor, rejected = rig
    failed, following = message("fail"), message("continue")
    bus.publish(failed)
    bus.publish(following)
    kernel.step()
    executor.complete(error=RuntimeError("simulated processor failure"))
    kernel.step()
    assert kernel.stats["failed"] == 1
    assert kernel.snapshot()["memory"][0]["ok"] is False
    assert (failed.id, "cognition_failed") in rejected
    assert executor.jobs[1][2][0].id == following.id


def test_downstream_work_retains_capacity_after_processor_returns():
    clock, executor, bus = Clock(), ControlledExecutor(), EventBus()
    ready = True
    kernel = MinimalBrainKernel(
        bus, receipt, clock=clock, executor=executor, processor_ready=lambda: ready,
    )
    try:
        bus.publish(message("first"))
        bus.publish(message("following"))
        kernel.step()
        ready = False
        executor.complete()
        kernel.step()
        assert kernel.stats["processor_busy"] is False
        assert kernel.stats["downstream_busy"] is True
        assert kernel.stats["pending"] == 1
        assert len(executor.jobs) == 1
        ready = True
        kernel.step()
        assert len(executor.jobs) == 2
        assert executor.jobs[1][2][0].payload["text"] == "following"
    finally:
        ready = True
        for future, _, _, _ in executor.jobs:
            if not future.done():
                future.set_result({"ok": True, "reply": "cleanup"})
        kernel.stop(timeout=0)


def test_stop_reports_physical_work_and_rejects_all_unstarted_requests(rig):
    kernel, bus, _, executor, rejected = rig
    active, queued, unread = message("active"), message("queued"), message("unread")
    bus.publish(active)
    bus.publish(queued)
    kernel.step()
    bus.publish(unread)
    assert kernel.stop(timeout=0) is False
    assert kernel.stats["processor_busy"] is True
    assert (queued.id, "kernel_stopped") in rejected
    assert (unread.id, "kernel_stopped") in rejected
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        kernel.start()
    executor.complete()
    assert kernel.stop(timeout=0) is True
    assert len(executor.jobs) == 1


def test_restore_interrupts_unfinished_goals_without_replaying_actions(rig):
    kernel, bus, _, _, _ = rig
    active, queued = message("active"), message("queued")
    bus.publish(active)
    bus.publish(queued)
    kernel.step()
    saved = kernel.snapshot()
    recovered_bus = EventBus()
    calls = []
    restored = MinimalBrainKernel(
        recovered_bus, lambda event, context: calls.append(event), initial_snapshot=saved,
    )
    try:
        recovered_bus.publish(active)
        recovered_bus.publish(queued)
        restored.step()
        state = restored.snapshot()
        assert {g["status"] for g in state["goals"]} == {"interrupted"}
        assert state["stats"]["interrupted"] == 2
        assert calls == []
        assert state["stats"]["duplicates"] == 2
        state["goals"][0]["status"] = "tampered"
        assert restored.snapshot()["goals"][0]["status"] == "interrupted"
    finally:
        restored.stop(timeout=1)
