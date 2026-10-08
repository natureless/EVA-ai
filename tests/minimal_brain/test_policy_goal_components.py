from dataclasses import FrozenInstanceError

import pytest

from event.event_schema import Event
from packages.minimal_brain.goals import GoalConflict, GoalLedger, GoalStatus
from packages.minimal_brain.policies import AttentionContext, Candidate, GlobalWorkspace


def candidate(event_id, *, kind="user_message", order=1):
    return Candidate(event_id, kind, 1, event_id, 0, order)


def test_resource_value_and_stable_attention_ranking_have_observable_effect():
    workspace = GlobalWorkspace(2)
    candidates = (
        candidate("first", order=1),
        candidate("second", order=2),
        candidate("cleanup", kind="maintenance", order=3),
    )
    workspace.select(candidates, AttentionContext(0, 0, 1))
    assert [item["event_id"] for item in workspace.snapshot()] == ["first", "second"]
    workspace.select(candidates, AttentionContext(0, 1, 1))
    assert [item["event_id"] for item in workspace.snapshot()] == ["cleanup", "first"]
    snapshot = workspace.snapshot()
    snapshot[0]["goal_version"] = 999
    assert workspace.first.goal_version == 1


@pytest.mark.parametrize(
    "selection", [("forged",), ("event", "event"), ("event", "extra"), ["event"]]
)
def test_attention_cannot_inject_duplicate_or_over_capacity_candidates(selection):
    class InvalidAttention:
        def select(self, candidates, capacity):
            return selection

    workspace = GlobalWorkspace(1)
    workspace.select((candidate("event"),), AttentionContext(0, 0, 0))
    workspace.attention_policy = InvalidAttention()
    with pytest.raises(ValueError, match="attention policy"):
        workspace.select((candidate("event"),), AttentionContext(0, 0, 0))
    assert workspace.first.event_id == "event"


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, "100"])
def test_invalid_value_score_never_enters_workspace(score):
    class InvalidValue:
        def score(self, candidate, context):
            return score

    workspace = GlobalWorkspace(1, value_policy=InvalidValue())
    with pytest.raises(ValueError, match="finite numeric"):
        workspace.select((candidate("event"),), AttentionContext(0, 0, 0))
    assert workspace.first is None


def test_goal_versions_and_terminal_states_cannot_be_overwritten():
    ledger = GoalLedger()
    event = Event(type="user_message", source="test")
    admitted = ledger.accept(event)
    running = ledger.transition(event.id, GoalStatus.RUNNING, expected_version=1)
    assert admitted.status == GoalStatus.QUEUED
    assert running.version == 2
    with pytest.raises(GoalConflict, match="version"):
        ledger.transition(event.id, GoalStatus.COMPLETED, expected_version=1)
    with pytest.raises(GoalConflict, match="invalid goal transition"):
        ledger.transition(event.id, GoalStatus.CANCELLED)
    completed = ledger.transition(event.id, GoalStatus.COMPLETED, expected_version=2)
    assert completed.version == 3
    assert completed.success_condition == "stable_processor_returns_success_receipt"
    with pytest.raises(GoalConflict):
        ledger.transition(event.id, GoalStatus.RUNNING)
    with pytest.raises(FrozenInstanceError):
        completed.status = GoalStatus.RUNNING


@pytest.mark.parametrize(
    "status", [GoalStatus.QUEUED, GoalStatus.RUNNING, GoalStatus.COMPLETED]
)
def test_restore_interrupts_only_unfinished_goals_and_preserves_contract(status):
    ledger = GoalLedger()
    event = Event(type="user_message", source="test")
    ledger.accept(event)
    if status != GoalStatus.QUEUED:
        ledger.transition(event.id, GoalStatus.RUNNING)
    if status == GoalStatus.COMPLETED:
        ledger.transition(event.id, status)
    saved = ledger[event.id].snapshot()
    restored = GoalLedger()
    goal, interrupted = restored.restore(saved)
    assert interrupted is (status != GoalStatus.COMPLETED)
    assert goal.status == (GoalStatus.INTERRUPTED if interrupted else status)
    assert goal.version == saved["version"] + int(interrupted)
    assert saved["status"] == status.value


def test_restore_rejects_invalid_goal_version_and_contract():
    ledger = GoalLedger()
    event = Event(type="user_message", source="test")
    raw = ledger.accept(event).snapshot()
    for field, value in (
        ("version", True),
        ("version", -1),
        ("status", "verified"),
        ("kind", "execute_anything"),
    ):
        invalid = {**raw, field: value}
        with pytest.raises(ValueError):
            GoalLedger().restore(invalid)
