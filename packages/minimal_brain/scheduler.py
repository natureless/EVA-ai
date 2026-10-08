"""Coordinator-owned, multi-timescale scheduling without catch-up bursts.

Callbacks are trusted, nonblocking state updates. They run on the caller's
thread; slow work must be submitted to a separately bounded execution slot.
Registration changes and ``run_due`` must share the owner's synchronization.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Callable


class NodePhase(str, Enum):
    BEFORE_GRAPH = "before_graph"
    AFTER_GRAPH = "after_graph"


@dataclass(frozen=True)
class NodeTick:
    now: float
    elapsed: float
    scheduled_at: float
    lateness: float
    skipped_periods: int
    invocation: int


@dataclass(frozen=True)
class PeriodicNode:
    name: str
    interval: float
    update: Callable[[NodeTick], None]
    phase: NodePhase = NodePhase.AFTER_GRAPH
    critical: bool = False
    ready: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("node name must be nonempty")
        if (
            isinstance(self.interval, bool)
            or not math.isfinite(self.interval)
            or self.interval <= 0
        ):
            raise ValueError("node interval must be finite and positive")
        if not callable(self.update) or not isinstance(self.phase, NodePhase):
            raise ValueError(
                "node update and phase must satisfy the scheduling contract"
            )
        if not isinstance(self.critical, bool) or (
            self.ready is not None and not callable(self.ready)
        ):
            raise ValueError("invalid node failure or readiness contract")


@dataclass
class _NodeState:
    spec: PeriodicNode
    next_due: float
    last_started_at: float | None = None
    runs: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    skipped_periods: int = 0
    readiness_waits: int = 0
    last_elapsed: float = 0.0
    last_lateness: float = 0.0
    last_duration_sec: float = 0.0
    last_error: str | None = None
    status: str = "not_started"


class MultiTimescaleScheduler:
    """Run each due node at most once per pass and expose its actual timing.

    The next deadline is measured from the actual start, so a suspended process
    does not replay hundreds of obsolete updates. ``elapsed`` is actual monotonic
    time since the previous invocation (zero for a new node), not its configured
    interval. A not-ready node retains its deadline until capacity becomes free.
    Failures of optional nodes are visible and isolated; critical failures propagate
    to the coordinator. Partial mutations inside a failed callback are not rolled back.
    """

    def __init__(self, *, max_nodes: int = 64) -> None:
        if (
            isinstance(max_nodes, bool)
            or not isinstance(max_nodes, int)
            or max_nodes < 1
        ):
            raise ValueError("max_nodes must be a positive integer")
        self._max_nodes = max_nodes
        self._nodes: OrderedDict[str, _NodeState] = OrderedDict()
        self._last_now: float | None = None

    def register(self, node: PeriodicNode, *, now: float) -> None:
        self._validate_time(now)
        if node.name in self._nodes:
            raise ValueError(f"node already registered: {node.name}")
        if len(self._nodes) >= self._max_nodes:
            raise ValueError("scheduler node capacity exceeded")
        self._nodes[node.name] = _NodeState(node, next_due=now)

    def unregister(self, name: str) -> bool:
        return self._nodes.pop(name, None) is not None

    def wake(self, name: str, *, now: float) -> None:
        self._validate_time(now)
        state = self._nodes[name]
        state.next_due = min(state.next_due, now)

    def run_due(self, *, now: float, phase: NodePhase) -> int:
        self._validate_time(now)
        self._last_now = now
        attempted = 0
        # New registrations become eligible next pass, never mid-iteration.
        for state in tuple(self._nodes.values()):
            node = state.spec
            if (
                self._nodes.get(node.name) is not state
                or node.phase != phase
                or now < state.next_due
            ):
                continue
            try:
                ready = node.ready is None or node.ready() is True
            except Exception as exc:
                state.next_due = now + node.interval
                self._failed(state, exc)
                attempted += 1
                if node.critical:
                    raise
                continue
            if not ready:
                state.readiness_waits += 1
                state.status = "waiting_for_capacity"
                continue
            lateness = max(0.0, now - state.next_due)
            tick = NodeTick(
                now=now,
                elapsed=0.0
                if state.last_started_at is None
                else now - state.last_started_at,
                scheduled_at=state.next_due,
                lateness=lateness,
                skipped_periods=math.floor(
                    math.nextafter(lateness / node.interval, math.inf)
                ),
                invocation=state.runs + 1,
            )
            state.next_due = now + node.interval
            state.last_started_at = now
            state.runs += 1
            state.last_elapsed = tick.elapsed
            state.last_lateness = tick.lateness
            state.skipped_periods += tick.skipped_periods
            attempted += 1
            started = time.perf_counter()
            try:
                node.update(tick)
            except Exception as exc:
                self._failed(state, exc)
                if node.critical:
                    raise
            else:
                state.consecutive_failures = 0
                state.last_error = None
                state.status = "healthy"
            finally:
                state.last_duration_sec = max(0.0, time.perf_counter() - started)
        return attempted

    @staticmethod
    def _failed(state: _NodeState, error: Exception) -> None:
        state.failures += 1
        state.consecutive_failures += 1
        state.status = "degraded"
        # Exception text can contain payloads or secrets; expose its type only.
        state.last_error = type(error).__name__

    def snapshot(self, *, now: float) -> dict[str, dict]:
        self._validate_time(now)
        return {
            name: {
                "interval": state.spec.interval,
                "phase": state.spec.phase.value,
                "critical": state.spec.critical,
                "next_due": state.next_due,
                "overdue_sec": max(0.0, now - state.next_due),
                "last_started_at": state.last_started_at,
                "runs": state.runs,
                "failures": state.failures,
                "consecutive_failures": state.consecutive_failures,
                "skipped_periods": state.skipped_periods,
                "readiness_waits": state.readiness_waits,
                "last_elapsed": state.last_elapsed,
                "last_lateness": state.last_lateness,
                "last_duration_sec": state.last_duration_sec,
                "last_error": state.last_error,
                "status": state.status,
            }
            for name, state in self._nodes.items()
        }

    def _validate_time(self, now: float) -> None:
        if isinstance(now, bool) or not math.isfinite(now):
            raise ValueError("scheduler time must be finite")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("scheduler clock moved backwards")
