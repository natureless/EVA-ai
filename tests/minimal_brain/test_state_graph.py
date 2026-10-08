"""Interventions on graph time, signal provenance, and temporary modulation."""

import math

import pytest

from packages.minimal_brain.state_graph import StateGraph


class Clock:
    def __init__(self):
        self.time = 0.0

    def __call__(self):
        return self.time


def graph_with_edge(*, delay=0.0, sign=1):
    clock = Clock()
    graph = StateGraph(clock)
    graph.add_node("body", tau=0.2, activation=0.5)
    graph.add_node("attention", tau=2.0, activation=0.5)
    edge_id = graph.connect("body", "attention", 1.0, delay=delay, sign=sign)
    return clock, graph, edge_id


def test_same_elapsed_time_has_same_response_despite_extra_polling():
    coarse_clock, coarse, edge_id = graph_with_edge(delay=0.5)
    fine_clock, fine, _ = graph_with_edge(delay=0.5)
    for graph in (coarse, fine):
        graph.publish("body", 1.0, ttl=2.0)
        graph.modulate(edge_id, gain=2.0, ttl=1.25)
    coarse_clock.time = 4.0
    coarse.advance()
    for step in range(1, 401):
        fine_clock.time = step / 100
        fine.advance()
    for node_id in ("body", "attention"):
        left = coarse.snapshot()["nodes"][node_id]
        right = fine.snapshot()["nodes"][node_id]
        assert left["activation"] == pytest.approx(right["activation"], abs=1e-13)
        assert left["version"] == right["version"]
    assert coarse.snapshot()["edges"] == fine.snapshot()["edges"]


def test_new_input_does_not_apply_to_the_past_idle_interval():
    clock = Clock()
    graph = StateGraph(clock)
    graph.add_node("world", tau=2.0, activation=0.5)
    clock.time = 100.0
    assert graph.set_input("world", math.log(3), source_version=5, observed_at=1.0)
    assert graph.snapshot()["nodes"]["world"]["activation"] == 0.5
    clock.time = 102.0
    graph.advance()
    assert graph.snapshot()["nodes"]["world"]["activation"] == pytest.approx(
        0.75 + (0.5 - 0.75) * math.exp(-1)
    )


def test_delay_and_signal_expiry_split_an_idle_interval():
    clock, graph, edge_id = graph_with_edge(delay=2.0)
    graph.publish("body", 1.0, source_version=8, ttl=3.0)
    clock.time = 1.999
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["signal"] == 0.0
    assert graph.snapshot()["nodes"]["attention"]["activation"] == 0.5
    clock.time = 2.0
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["signal"] == 1.0
    assert graph.snapshot()["nodes"]["attention"]["activation"] == 0.5
    clock.time = 4.0
    graph.advance()
    high_target = 1 / (1 + math.exp(-1))
    after_signal = high_target + (0.5 - high_target) * math.exp(-0.5)
    expected = 0.5 + (after_signal - 0.5) * math.exp(-0.5)
    assert graph.snapshot()["nodes"]["attention"]["activation"] == pytest.approx(expected)
    assert graph.snapshot()["edges"][edge_id]["signal"] == 0.0
    assert graph.snapshot()["edges"][edge_id]["signal_version"] == 8


def test_signal_expiring_before_visibility_never_arrives():
    clock, graph, edge_id = graph_with_edge(delay=2.0)
    graph.publish("body", 1.0, ttl=1.0)
    clock.time = 4.0
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["signal_version"] == -1
    assert graph.snapshot()["nodes"]["attention"]["activation"] == 0.5
    assert graph.snapshot()["pending_events"] == 0


def test_late_publication_applies_at_acceptance_and_old_expiry_cannot_clear_new_signal():
    clock, graph, edge_id = graph_with_edge(delay=1.0)
    graph.publish("body", 1.0, source_version=1, ttl=4.0)
    clock.time = 2.0
    graph.advance()
    graph.publish("body", 0.8, source_version=2, ttl=6.0)
    clock.time = 4.0
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["signal"] == 0.8
    clock.time = 20.0
    graph.advance()
    before = graph.snapshot()["nodes"]["attention"]["activation"]
    assert graph.publish("body", 0.7, source_version=3, emitted_at=10.0, ttl=20.0)
    assert graph.snapshot()["nodes"]["attention"]["activation"] == before
    assert graph.snapshot()["edges"][edge_id]["signal"] == 0.7
    assert not graph.publish("body", 0.9, source_version=4, emitted_at=9.0)
    assert not graph.publish("body", 0.9, source_version=2)


def test_modulations_expire_individually_without_changing_base_weight():
    clock, graph, edge_id = graph_with_edge()
    graph.publish("body", 1.0)
    graph.modulate(edge_id, gain=2.0, ttl=1.0, source="risk", gate=0.8)
    graph.modulate(edge_id, gain=0.5, ttl=3.0, source="budget", gate=0.4)
    edge = graph.snapshot()["edges"][edge_id]
    assert edge["gain"] == 1.5
    assert edge["gate"] == 0.4
    assert edge["base_weight"] == 1.0
    clock.time = 1.0
    graph.advance()
    edge = graph.snapshot()["edges"][edge_id]
    assert edge["gain"] == 0.5
    assert edge["gate"] == 0.4
    assert set(edge["modulations"]) == {"budget"}
    assert graph.revoke_modulation(edge_id, source="budget")
    assert not graph.revoke_modulation(edge_id, source="budget")
    edge = graph.snapshot()["edges"][edge_id]
    assert edge["gain"] == edge["gate"] == edge["base_weight"] == 1.0
    clock.time = 3.0
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["modulations"] == {}


def test_refreshing_one_modulation_prevents_its_old_timer_revoking_it():
    clock, graph, edge_id = graph_with_edge()
    graph.modulate(edge_id, gain=2.0, ttl=1.0, source="risk")
    clock.time = 0.5
    graph.modulate(edge_id, gain=3.0, ttl=2.0, source="risk")
    clock.time = 1.0
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["gain"] == 3.0
    clock.time = 2.5
    graph.advance()
    assert graph.snapshot()["edges"][edge_id]["gain"] == 1.0


def test_inhibitory_sign_is_applied_once_and_advance_does_not_publish():
    clock, graph, edge_id = graph_with_edge(sign=-1)
    graph.set_input("body", 20.0)
    clock.time = 1.0
    graph.advance()
    assert graph.snapshot()["nodes"]["attention"]["activation"] == 0.5
    assert graph.snapshot()["edges"][edge_id]["signal"] == 0.0
    graph.publish("body")
    assert graph.snapshot()["edges"][edge_id]["signal"] > 0.9
    assert graph.snapshot()["nodes"]["attention"]["target"] < 0.5
    clock.time = 2.0
    graph.advance()
    assert graph.snapshot()["nodes"]["attention"]["activation"] < 0.5


def test_old_version_or_older_observation_cannot_replace_current_input():
    clock, graph, _ = graph_with_edge()
    clock.time = 5.0
    assert graph.set_input("body", 2.0, source_version=10, observed_at=4.0)
    clock.time = 6.0
    assert not graph.set_input("body", -2.0, source_version=9, observed_at=5.0)
    assert not graph.set_input("body", -2.0, source_version=11, observed_at=3.0)
    assert not graph.set_input("body", -2.0, source_version=10, observed_at=6.0)
    node = graph.snapshot()["nodes"]["body"]
    assert node["input"] == 2.0
    assert node["input_version"] == 10
    assert node["observed_at"] == 4.0
    assert graph.snapshot()["time"] == 6.0


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_or_boolean_parameters_are_rejected_without_mutation(invalid):
    clock, graph, edge_id = graph_with_edge()
    before = graph.snapshot()
    operations = [
        lambda: graph.set_input("body", invalid),
        lambda: graph.publish("body", invalid),
        lambda: graph.modulate(edge_id, gain=invalid, ttl=1.0),
        lambda: graph.modulate(edge_id, gain=1.0, ttl=invalid),
        lambda: graph.modulate(edge_id, gain=1.0, gate=invalid, ttl=1.0),
        lambda: graph.connect("body", "attention", invalid, edge_id="other"),
        lambda: graph.add_node("other", tau=invalid),
    ]
    for operation in operations:
        with pytest.raises(ValueError):
            operation()
        assert graph.snapshot() == before
    clock.time = invalid
    with pytest.raises(ValueError):
        graph.advance()


def test_clock_cannot_rewind_and_snapshot_is_detached():
    clock, graph, _ = graph_with_edge()
    clock.time = 2.0
    state = graph.advance()
    state["nodes"]["body"]["input"] = 1000.0
    assert graph.snapshot()["nodes"]["body"]["input"] == 0.0
    clock.time = 1.0
    with pytest.raises(ValueError, match="backwards"):
        graph.advance()
    assert graph.snapshot()["time"] == 2.0


def test_invalid_ranges_and_versions_fail_before_mutating_state():
    _, graph, edge_id = graph_with_edge()
    before = graph.snapshot()
    operations = [
        lambda: graph.add_node("zero_tau", tau=0),
        lambda: graph.add_node("bad_activation", activation=1.1),
        lambda: graph.connect("body", "attention", -1, edge_id="negative_weight"),
        lambda: graph.connect("body", "attention", 1, sign=0, edge_id="bad_sign"),
        lambda: graph.connect("body", "attention", 1, delay=-1, edge_id="bad_delay"),
        lambda: graph.set_input("body", 1, source_version=-1),
        lambda: graph.set_input("body", 1, source_version=True),
        lambda: graph.set_input("body", 1, observed_at=1),
        lambda: graph.publish("body", 1.1),
        lambda: graph.publish("body", 1, ttl=0),
        lambda: graph.modulate(edge_id, gain=5, ttl=1),
        lambda: graph.modulate(edge_id, gain=1, gate=-0.1, ttl=1),
    ]
    for operation in operations:
        with pytest.raises(ValueError):
            operation()
        assert graph.snapshot() == before


def test_extreme_finite_inputs_remain_bounded_without_nonfinite_targets():
    clock = Clock()
    graph = StateGraph(clock)
    graph.add_node("positive", baseline=1e308, tau=1e-308)
    graph.add_node("cancelled", baseline=1e308)
    graph.add_node("negative", baseline=-1e308)
    graph.set_input("positive", 1e308)
    graph.set_input("cancelled", -1e308)
    graph.set_input("negative", -1e308)
    clock.time = 10.0
    for node in graph.advance()["nodes"].values():
        assert 0.0 <= node["activation"] <= 1.0
        assert 0.0 <= node["target"] <= 1.0
    assert graph.snapshot()["nodes"]["cancelled"]["target"] == 0.5


def test_feedback_only_runs_at_explicit_samples_and_quiet_advance_adds_no_events():
    clock, graph, _ = graph_with_edge(delay=0.1)
    graph.connect("attention", "body", 2.0, sign=-1, delay=0.1)
    for step in range(50):
        clock.time = step * 0.2
        graph.advance()
        graph.publish("body", ttl=0.3)
        graph.publish("attention", ttl=0.3)
        assert graph.snapshot()["pending_events"] <= 6
    clock.time = 1000.0
    snapshot = graph.advance()
    assert snapshot["pending_events"] == 0
    for node in snapshot["nodes"].values():
        assert node["activation"] == pytest.approx(0.5)
