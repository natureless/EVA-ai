from concurrent.futures import Executor, Future

import pytest

from event.event_bus import EventBus
from event.event_schema import Event
from packages.minimal_brain.kernel import KernelConfig, MinimalBrainKernel
from packages.minimal_brain.scheduler import PeriodicNode


class ControlledExecutor(Executor):
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_running_or_notify_cancel()
        self.jobs.append((future, args))
        return future


def message(text):
    return Event(type="user_message", source="test", payload={"text": text})


@pytest.fixture
def rig():
    now, bus, executor = [0.0], EventBus(), ControlledExecutor()
    rejected = []
    kernel = MinimalBrainKernel(
        bus,
        lambda event, context: {"ok": True},
        clock=lambda: now[0],
        executor=executor,
        on_reject=lambda event, reason: rejected.append((event.id, reason)),
    )
    yield kernel, now, bus, executor, rejected
    for future, _ in executor.jobs:
        if not future.done():
            future.set_result({"ok": True})
    assert kernel.stop(timeout=0)


def test_registered_node_uses_actual_elapsed_while_cognition_is_busy(rig):
    kernel, now, bus, executor, _ = rig
    ticks = []
    kernel.register_node(PeriodicNode("custom", 0.125, ticks.append))
    bus.publish(message("hold executor"))
    kernel.step()
    now[0] = 5
    kernel.step()
    assert len(ticks) == 2
    assert ticks[-1].elapsed == 5
    assert kernel.stats["processor_busy"] is True
    health = kernel.snapshot()["stats"]["nodes"]["custom"]
    assert health["runs"] == 2
    assert health["skipped_periods"] == 39
    assert kernel.unregister_node("custom") is True
    with pytest.raises(ValueError, match="built-in"):
        kernel.unregister_node("body")
    assert len(executor.jobs) == 1


def test_optional_node_fault_is_reported_without_stopping_other_nodes(rig):
    kernel, _, bus, executor, _ = rig

    def fail(tick):
        raise RuntimeError("optional sensor failed")

    kernel.register_node(PeriodicNode("optional", 1, fail))
    bus.publish(message("can still run"))
    kernel.step()
    assert len(executor.jobs) == 1
    assert kernel.stats["closed"] is False
    assert kernel.stats["nodes"]["optional"]["status"] == "degraded"


def test_request_stop_is_nonblocking_and_defers_rejection_until_final_stop(rig):
    kernel, _, bus, executor, rejected = rig
    active, queued, unread = message("active"), message("queued"), message("unread")
    bus.publish(active)
    bus.publish(queued)
    kernel.step()
    bus.publish(unread)
    kernel.request_stop()
    kernel.request_stop()
    kernel.step()
    assert len(executor.jobs) == 1
    assert bus.size() == 1
    assert not rejected
    with pytest.raises(RuntimeError, match="stopped"):
        kernel.register_node(PeriodicNode("late", 1, lambda tick: None))
    assert kernel.stop(timeout=0) is False
    assert (queued.id, "kernel_stopped") in rejected
    assert (unread.id, "kernel_stopped") in rejected


def test_injected_value_and_attention_policies_control_real_selection_with_capacity_gate():
    class PreferLaterValue:
        def score(self, candidate, context):
            return float(candidate.order)

    class PickLowestValue:
        def select(self, candidates, capacity):
            ranked = sorted(candidates, key=lambda item: item.score)
            return tuple(item.candidate.event_id for item in ranked[:capacity])

    bus, executor = EventBus(), ControlledExecutor()
    ready = [False]
    kernel = MinimalBrainKernel(
        bus,
        lambda event, context: {"ok": True},
        executor=executor,
        value_policy=PreferLaterValue(),
        attention_policy=PickLowestValue(),
        processor_ready=lambda: ready[0],
        config=KernelConfig(workspace_capacity=1),
    )
    first, second = message("first"), message("second")
    try:
        bus.publish(first)
        bus.publish(second)
        kernel.step()
        workspace = kernel.snapshot()["workspace"]
        assert len(workspace) == 1
        assert workspace[0]["event_id"] == first.id
        assert workspace[0]["score"] == 1
        assert not executor.jobs
        ready[0] = True
        kernel.step()
        assert executor.jobs[0][1][0].id == first.id
    finally:
        ready[0] = True
        for future, _ in executor.jobs:
            if not future.done():
                future.set_result({"ok": True})
        assert kernel.stop(timeout=0)
