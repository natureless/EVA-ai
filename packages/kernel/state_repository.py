"""Revision-aware state repository used by the MVSC runtime and tests.

The repository deliberately keeps the state commit boundary small: callers either
submit a complete state with an expected previous revision, or submit a
``StateDelta`` whose base revision is checked and applied atomically. The sibling
SQLite repository preserves this contract and adds atomic action intents; the
application's default consumer has not switched to that durable repository.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

from packages.contracts.events import EventEnvelope
from packages.contracts.state import (
    ConsciousState,
    StateConflictError,
    StateDelta,
)


class InMemoryStateRepository:
    """Small authoritative repository with optimistic revision checks.

    It is intended for the current single-process runtime, integration tests,
    and as an executable reference for the future durable repository.  Returned
    models and replay events are copies, so a reader cannot mutate authority
    without going through a commit.
    """

    def __init__(self, initial_state: ConsciousState | None = None) -> None:
        self._states: dict[str, ConsciousState] = {}
        self._events: dict[str, list[tuple[int, EventEnvelope]]] = {}
        self._lock = asyncio.Lock()
        if initial_state is not None:
            self._states[initial_state.subject_id] = initial_state.model_copy(deep=True)
            self._events[initial_state.subject_id] = []

    async def load(self, subject_id: str) -> ConsciousState:
        async with self._lock:
            state = self._states.get(subject_id)
            if state is None:
                state = ConsciousState(subject_id=subject_id)
                self._states[subject_id] = state
                self._events[subject_id] = []
            return state.model_copy(deep=True)

    async def commit(
        self,
        previous_version: int,
        new_state: ConsciousState,
        events: list[EventEnvelope],
    ) -> int:
        """Commit a complete state if its expected revision is current."""
        async with self._lock:
            current = self._states.get(new_state.subject_id)
            if current is None:
                current = ConsciousState(subject_id=new_state.subject_id)
                self._states[new_state.subject_id] = current
                self._events[new_state.subject_id] = []
            self._check_expected_version(current, previous_version, new_state.version)
            if new_state.subject_id != current.subject_id:
                raise ValueError("state subject_id cannot change during commit")
            if new_state.integrity_hash != new_state.compute_integrity_hash():
                raise ValueError(
                    "new_state.integrity_hash does not match state contents"
                )
            self._store_locked(current.subject_id, new_state, events)
            return new_state.version

    async def commit_delta(
        self,
        delta: StateDelta,
        events: list[EventEnvelope] | None = None,
        subject_id: str = "eva-001",
    ) -> int:
        """Apply and commit a delta under one lock acquisition."""
        async with self._lock:
            current = self._states.get(subject_id)
            if current is None:
                current = ConsciousState(subject_id=subject_id)
                self._states[subject_id] = current
                self._events[subject_id] = []
            new_state = delta.apply(current)
            self._store_locked(subject_id, new_state, events or [])
            return new_state.version

    async def replay(
        self,
        subject_id: str,
        from_version: int = 0,
        to_version: int | None = None,
    ) -> list[EventEnvelope]:
        async with self._lock:
            records = self._events.get(subject_id, [])
            return [
                deepcopy(event)
                for version, event in records
                if version > from_version
                and (to_version is None or version <= to_version)
            ]

    @staticmethod
    def _check_expected_version(
        current: ConsciousState,
        previous_version: int,
        new_version: int,
    ) -> None:
        if current.version != previous_version:
            raise StateConflictError(
                f"state version {current.version} does not match expected "
                f"version {previous_version}"
            )
        if new_version != previous_version + 1:
            raise ValueError(
                "new_state.version must equal previous_version + 1 "
                f"(got {new_version}, expected {previous_version + 1})"
            )

    def _store_locked(
        self,
        subject_id: str,
        state: ConsciousState,
        events: list[EventEnvelope],
    ) -> None:
        self._states[subject_id] = state.model_copy(deep=True)
        records = self._events.setdefault(subject_id, [])
        records.extend((state.version, deepcopy(event)) for event in events)


__all__ = ["InMemoryStateRepository"]
