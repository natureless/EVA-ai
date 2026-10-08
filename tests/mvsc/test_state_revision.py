"""STA-01: revision-aware StateDelta and atomic state commits."""

import asyncio

import pytest

from packages.contracts.events import EventEnvelope
from packages.contracts.state import (
    ConsciousState,
    StateConflictError,
    StateDelta,
)
from packages.kernel.state_repository import InMemoryStateRepository


def test_delta_advances_revision_without_mutating_base_state():
    state = ConsciousState(subject_id="test-eva")
    delta = StateDelta(
        base_version=0,
        source="perception",
        changes={"world": {"focus": "new input"}},
        advance_tick=True,
    )

    updated = delta.apply(state)

    assert state.version == 0
    assert state.tick == 0
    assert updated.version == 1
    assert updated.tick == 1
    assert updated.world == {"focus": "new input"}
    assert updated.integrity_hash == updated.compute_integrity_hash()


def test_delta_rejects_stale_revision_and_reserved_fields():
    state = ConsciousState(version=2)

    with pytest.raises(StateConflictError, match="does not match"):
        StateDelta(base_version=1, source="old", changes={"world": {}}).apply(state)

    with pytest.raises(ValueError, match="reserved"):
        StateDelta(base_version=2, source="bad", changes={"version": 99}).apply(state)

    with pytest.raises(ValueError, match="reserved"):
        StateDelta(base_version=2, source="bad", changes={"tick": 99}).apply(state)

    with pytest.raises(ValueError, match="unknown"):
        StateDelta(base_version=2, source="bad", changes={"not_a_state_field": 1}).apply(state)


@pytest.mark.asyncio
async def test_repository_rejects_stale_complete_state():
    repo = InMemoryStateRepository(ConsciousState(subject_id="test-eva"))
    first = await repo.load("test-eva")
    first = first.apply(world_delta={"focus": "first"})
    assert await repo.commit(0, first, []) == 1

    stale = ConsciousState(subject_id="test-eva", version=1, world={"focus": "stale"})
    with pytest.raises(StateConflictError):
        await repo.commit(0, stale, [])

    current = await repo.load("test-eva")
    assert current.version == 1
    assert current.world["focus"] == "first"


@pytest.mark.asyncio
async def test_repository_rejects_tampered_integrity_hash():
    repo = InMemoryStateRepository(ConsciousState(subject_id="test-eva"))
    candidate = (await repo.load("test-eva")).apply(world_delta={"focus": "ok"})
    candidate.integrity_hash = "tampered"

    with pytest.raises(ValueError, match="integrity_hash"):
        await repo.commit(0, candidate, [])

    assert (await repo.load("test-eva")).version == 0


@pytest.mark.asyncio
async def test_concurrent_deltas_only_one_wins():
    repo = InMemoryStateRepository(ConsciousState(subject_id="test-eva"))
    first = StateDelta(
        base_version=0,
        source="planner",
        changes={"world": {"winner": "one"}},
    )
    second = StateDelta(
        base_version=0,
        source="reviewer",
        changes={"world": {"winner": "two"}},
    )

    results = await asyncio.gather(
        repo.commit_delta(first, subject_id="test-eva"),
        repo.commit_delta(second, subject_id="test-eva"),
        return_exceptions=True,
    )

    assert sorted(result for result in results if isinstance(result, int)) == [1]
    assert sum(isinstance(result, StateConflictError) for result in results) == 1
    current = await repo.load("test-eva")
    assert current.version == 1
    assert current.world["winner"] in {"one", "two"}


@pytest.mark.asyncio
async def test_repository_replay_returns_events_for_committed_versions():
    repo = InMemoryStateRepository(ConsciousState(subject_id="test-eva"))
    event = EventEnvelope(event_type="test.input", source="test", subject_id="test-eva")
    delta = StateDelta(base_version=0, source="test", changes={"world": {"ok": True}})

    await repo.commit_delta(delta, events=[event], subject_id="test-eva")
    replayed = await repo.replay("test-eva", from_version=0)

    assert len(replayed) == 1
    assert replayed[0].event_id == event.event_id
    replayed[0].payload["mutated"] = True
    assert (await repo.replay("test-eva"))[0].payload == {}
