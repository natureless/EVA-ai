from dataclasses import FrozenInstanceError

import pytest

from packages.minimal_brain.scheduler import (
    MultiTimescaleScheduler,
    NodePhase,
    PeriodicNode,
)


def run(scheduler, now):
    scheduler.run_due(now=now, phase=NodePhase.BEFORE_GRAPH)
    scheduler.run_due(now=now, phase=NodePhase.AFTER_GRAPH)


def test_independent_deadlines_pass_actual_elapsed_without_catch_up_storm():
    scheduler = MultiTimescaleScheduler()
    fast, slow = [], []
    scheduler.register(PeriodicNode("fast", 0.125, fast.append), now=0)
    scheduler.register(PeriodicNode("slow", 1.0, slow.append), now=0)
    run(scheduler, 0)
    run(scheduler, 0.125)
    run(scheduler, 0.25)
    assert [tick.elapsed for tick in fast] == [0, 0.125, 0.125]
    assert len(slow) == 1
    run(scheduler, 10.25)
    assert len(fast) == 4
    assert len(slow) == 2
    assert fast[-1].elapsed == 10.0
    assert slow[-1].elapsed == 10.25
    assert fast[-1].skipped_periods == 79
    run(scheduler, 10.25)
    assert len(fast) == 4
    assert scheduler.snapshot(now=10.25)["fast"]["next_due"] == 10.375
    with pytest.raises(FrozenInstanceError):
        fast[-1].elapsed = 0


def test_graph_phase_and_capacity_readiness_are_independent():
    scheduler = MultiTimescaleScheduler()
    order, starts = [], []
    ready = False
    scheduler.register(
        PeriodicNode("slow", 1, starts.append, ready=lambda: ready), now=0
    )
    scheduler.register(
        PeriodicNode("post", 1, lambda tick: order.append("post")), now=0
    )
    scheduler.register(
        PeriodicNode(
            "pre",
            1,
            lambda tick: order.append("pre"),
            phase=NodePhase.BEFORE_GRAPH,
        ),
        now=0,
    )
    scheduler.run_due(now=0, phase=NodePhase.BEFORE_GRAPH)
    order.append("graph")
    scheduler.run_due(now=0, phase=NodePhase.AFTER_GRAPH)
    assert order == ["pre", "graph", "post"]
    assert not starts
    assert scheduler.snapshot(now=0)["slow"]["status"] == "waiting_for_capacity"
    ready = True
    run(scheduler, 0.25)
    assert starts[0].now == 0.25
    assert starts[0].scheduled_at == 0
    assert scheduler.snapshot(now=0.25)["slow"]["next_due"] == 1.25


def test_optional_node_failure_is_observable_and_recovers_on_its_next_deadline():
    scheduler = MultiTimescaleScheduler()
    calls, healthy = [], []

    def faulty(tick):
        calls.append(tick)
        if len(calls) == 1:
            raise RuntimeError("do not expose secret payload in health")

    scheduler.register(PeriodicNode("optional", 1, faulty), now=0)
    scheduler.register(PeriodicNode("healthy", 1, healthy.append), now=0)
    run(scheduler, 0)
    state = scheduler.snapshot(now=0)["optional"]
    assert state["status"] == "degraded"
    assert state["last_error"] == "RuntimeError"
    assert state["consecutive_failures"] == 1
    assert len(healthy) == 1
    run(scheduler, 0.5)
    assert len(calls) == 1
    run(scheduler, 1)
    state = scheduler.snapshot(now=1)["optional"]
    assert state["status"] == "healthy"
    assert state["failures"] == 1
    assert state["consecutive_failures"] == 0
    assert state["last_elapsed"] == 1


def test_critical_failure_propagates_and_is_not_immediately_retried():
    scheduler = MultiTimescaleScheduler()

    def broken(tick):
        raise ValueError("critical")

    scheduler.register(PeriodicNode("critical", 1, broken, critical=True), now=0)
    with pytest.raises(ValueError, match="critical"):
        run(scheduler, 0)
    run(scheduler, 0)
    assert scheduler.snapshot(now=0)["critical"]["failures"] == 1


def test_registry_is_bounded_and_rejects_duplicate_or_invalid_nodes():
    scheduler = MultiTimescaleScheduler(max_nodes=1)
    scheduler.register(PeriodicNode("first", 1, lambda tick: None), now=0)
    with pytest.raises(ValueError, match="already registered"):
        scheduler.register(PeriodicNode("first", 1, lambda tick: None), now=0)
    with pytest.raises(ValueError, match="capacity"):
        scheduler.register(PeriodicNode("second", 1, lambda tick: None), now=0)
    assert scheduler.unregister("first") is True
    assert scheduler.unregister("first") is False
    for interval in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            PeriodicNode("bad", interval, lambda tick: None)


def test_clock_regression_is_rejected_without_rewinding_deadlines():
    scheduler = MultiTimescaleScheduler()
    calls = []
    scheduler.register(PeriodicNode("node", 1, calls.append), now=2)
    run(scheduler, 2)
    with pytest.raises(ValueError, match="backwards"):
        run(scheduler, 1)
    assert len(calls) == 1
    assert scheduler.snapshot(now=2)["node"]["next_due"] == 3


def test_unregister_during_a_pass_prevents_removed_callback_from_running():
    scheduler = MultiTimescaleScheduler()
    removed_calls = []
    scheduler.register(
        PeriodicNode(
            "remover",
            1,
            lambda tick: scheduler.unregister("removed"),
        ),
        now=0,
    )
    scheduler.register(PeriodicNode("removed", 1, removed_calls.append), now=0)
    run(scheduler, 0)
    assert not removed_calls
    assert "removed" not in scheduler.snapshot(now=0)


def test_skipped_period_count_handles_decimal_intervals():
    scheduler = MultiTimescaleScheduler()
    calls = []
    scheduler.register(PeriodicNode("decimal", 0.1, calls.append), now=0)
    run(scheduler, 0)
    run(scheduler, 1.3)
    assert calls[-1].skipped_periods == 12
