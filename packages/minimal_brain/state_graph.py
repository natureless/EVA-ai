"""Deterministic, single-writer activation graph for the minimal brain experiment.

Inputs and delivered signals are held between events. Each interval has an exact
exponential response, with temporary modulation and signal expiry splitting the
interval at their scheduled times. ``advance`` never emits new samples: a caller
must explicitly ``publish`` at its declared sampling times. Consequently polling
more frequently does not silently change the feedback model.

This is a bounded control model, not a biological model or a proof of stability.
There are no worker threads, permissions, storage, or external actions here.
"""

from __future__ import annotations

import heapq
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Callable


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _version(value: int | None, previous: int) -> int:
    if value is None:
        return previous + 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("source_version must be a nonnegative integer")
    return value


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _sigmoid_sum(terms: list[float]) -> float:
    # Scaling prevents finite, extreme inputs from producing inf - inf or NaN.
    scale = max(1.0, *(abs(term) for term in terms))
    total = math.fsum(term / scale for term in terms)
    if total > 0 and total > 745.0 / scale:
        return 1.0
    if total < 0 and total < -745.0 / scale:
        return 0.0
    value = total * scale
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


@dataclass
class _Node:
    tau: float
    baseline: float
    activation: float
    target: float
    input: float = 0.0
    version: int = 0
    input_version: int = -1
    observed_at: float | None = None
    output_version: int = -1
    emitted_at: float | None = None


@dataclass(frozen=True)
class _Modulation:
    gain: float
    gate: float
    expires_at: float
    token: int


@dataclass
class _Edge:
    source: str
    target: str
    base_weight: float
    sign: int
    delay: float
    gain: float = 1.0
    gate: float = 1.0
    signal: float = 0.0
    signal_version: int = -1
    signal_expires_at: float | None = None
    modulations: dict[str, _Modulation] = field(default_factory=dict)

    @property
    def effective_weight(self) -> float:
        return self.sign * self.base_weight * self.gate * self.gain


class StateGraph:
    """Serialized graph with a shared injected monotonic clock.

    All public mutations must be called by one owner. ``snapshot`` is a detached
    read of the last committed time; call ``advance`` first to observe clock time.
    Explicit publication is the only way activations enter outgoing connections.
    Signals are normalized to [0, 1]; external inputs are finite sigmoid logits.
    Node ``version`` counts discrete commits, while snapshot ``time`` identifies
    the activation projection. A version alone is not an optimistic-lock token.

    Independent modulation sources combine as
    ``gain = clamp(1 + sum(source_gain - 1), 0, gain_max)`` and
    ``gate = min(source_gates, default=1)``. Expiring/revoking one source preserves
    the other sources. Neither operation edits the immutable base weight.
    """

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, *, gain_max: float = 4.0
    ) -> None:
        self._gain_max = _finite(gain_max, "gain_max")
        if self._gain_max < 1.0 or self._gain_max > 1_000_000.0:
            raise ValueError("gain_max must be in [1, 1000000]")
        self._clock = clock
        self._time = _finite(clock(), "clock")
        if self._time < 0:
            raise ValueError("clock must be nonnegative")
        self._nodes: dict[str, _Node] = {}
        self._edges: dict[str, _Edge] = {}
        self._outgoing: dict[str, list[str]] = {}
        self._incoming: dict[str, list[str]] = {}
        self._events: list[tuple[float, int, str, str, object]] = []
        self._sequence = 0

    def _now(self) -> float:
        now = _finite(self._clock(), "clock")
        if now < self._time:
            raise ValueError("monotonic clock moved backwards")
        return now

    def _deadline(self, start: float, duration: float) -> float:
        return _finite(start + duration, "scheduled time")

    def _schedule(self, due: float, kind: str, key: str, payload: object) -> int:
        self._sequence += 1
        heapq.heappush(self._events, (due, self._sequence, kind, key, payload))
        return self._sequence

    def add_node(
        self, node_id: str, tau: float = 1.0, baseline: float = 0.0,
        *, activation: float = 0.0,
    ) -> None:
        node_id = _identifier(node_id, "node_id")
        tau, baseline = _finite(tau, "tau"), _finite(baseline, "baseline")
        activation = _finite(activation, "activation")
        if tau <= 0 or not 0.0 <= activation <= 1.0:
            raise ValueError("tau must be positive and activation must be in [0, 1]")
        if node_id in self._nodes:
            raise ValueError(f"duplicate node: {node_id}")
        self._advance(self._now())
        self._nodes[node_id] = _Node(tau, baseline, activation, _sigmoid_sum([baseline]))
        self._outgoing[node_id], self._incoming[node_id] = [], []

    def connect(
        self, source: str, target: str, weight: float, sign: int = 1,
        delay: float = 0.0, *, edge_id: str | None = None,
    ) -> str:
        if source not in self._nodes or target not in self._nodes:
            raise KeyError("both connection endpoints must exist")
        weight, delay = _finite(weight, "weight"), _finite(delay, "delay")
        if weight < 0 or weight > sys.float_info.max / self._gain_max:
            raise ValueError("weight must be nonnegative and safe at maximum gain")
        if isinstance(sign, bool) or sign not in (-1, 1) or delay < 0:
            raise ValueError("sign must be -1 or +1 and delay must be nonnegative")
        edge_id = _identifier(edge_id if edge_id is not None else f"{source}->{target}", "edge_id")
        if edge_id in self._edges:
            raise ValueError(f"duplicate edge: {edge_id}")
        self._advance(self._now())
        self._edges[edge_id] = _Edge(source, target, weight, int(sign), delay)
        self._outgoing[source].append(edge_id)
        self._incoming[target].append(edge_id)
        return edge_id

    def set_input(
        self, node_id: str, value: float, source_version: int | None = None,
        *, observed_at: float | None = None,
    ) -> bool:
        """Accept an external observation now, never backdate its influence.

        Older versions or observation timestamps return False. A fresh but late
        observation is applied at acceptance time, with its actual time retained.
        """
        node = self._nodes[node_id]
        value = _finite(value, "input")
        version = _version(source_version, node.input_version)
        now = self._now()
        observed = now if observed_at is None else _finite(observed_at, "observed_at")
        if observed < 0 or observed > now:
            raise ValueError("observed_at must be between zero and the current time")
        self._advance(now)
        if version <= node.input_version or (node.observed_at is not None and observed < node.observed_at):
            return False
        node.input, node.input_version, node.observed_at = value, version, observed
        node.version += 1
        self._retarget(node_id)
        return True

    def publish(
        self, node_id: str, value: float | None = None,
        source_version: int | None = None, *, emitted_at: float | None = None,
        ttl: float | None = None,
    ) -> bool:
        """Sample activation (or an explicit [0,1] output) onto outgoing edges.

        Delivery is at max(acceptance time, emitted_at + delay); TTL starts at
        emitted_at. A late sample cannot retroactively change state. A TTL that
        ends before delivery causes that edge's delivery to be discarded.
        """
        node = self._nodes[node_id]
        if value is not None:
            value = _finite(value, "signal")
            if not 0 <= value <= 1:
                raise ValueError("signal must be in [0, 1]")
        version = _version(source_version, node.output_version)
        now = self._now()
        emitted = now if emitted_at is None else _finite(emitted_at, "emitted_at")
        if emitted < 0 or emitted > now:
            raise ValueError("emitted_at must be between zero and the current time")
        expires = None
        if ttl is not None:
            ttl = _finite(ttl, "ttl")
            if ttl <= 0:
                raise ValueError("ttl must be positive")
            expires = self._deadline(emitted, ttl)
        deliveries = [
            (edge_id, max(now, self._deadline(emitted, self._edges[edge_id].delay)))
            for edge_id in self._outgoing[node_id]
        ]
        self._advance(now)
        if version <= node.output_version or (node.emitted_at is not None and emitted < node.emitted_at):
            return False
        if expires is not None and expires <= now:
            return False
        value = node.activation if value is None else value
        node.output_version, node.emitted_at = version, emitted
        node.version += 1
        for edge_id, due in deliveries:
            if expires is not None and expires <= due:
                continue
            self._schedule(due, "signal", edge_id, (value, version, expires))
        self._advance(now)
        return True

    def modulate(
        self, edge_id: str, gain: float, ttl: float, *, source: str = "default",
        gate: float = 1.0,
    ) -> None:
        edge = self._edges[edge_id]
        source = _identifier(source, "modulation source")
        gain, gate, ttl = _finite(gain, "gain"), _finite(gate, "gate"), _finite(ttl, "ttl")
        if not 0 <= gain <= self._gain_max or not 0 <= gate <= 1 or ttl <= 0:
            raise ValueError("gain/gate must be within declared bounds and ttl positive")
        now = self._now()
        expires = self._deadline(now, ttl)
        self._advance(now)
        token = self._schedule(expires, "modulation_expiry", edge_id, source)
        edge.modulations[source] = _Modulation(gain, gate, expires, token)
        self._remodulate(edge)

    def revoke_modulation(self, edge_id: str, *, source: str = "default") -> bool:
        edge = self._edges[edge_id]
        source = _identifier(source, "modulation source")
        self._advance(self._now())
        if edge.modulations.pop(source, None) is None:
            return False
        self._remodulate(edge)
        return True

    def _remodulate(self, edge: _Edge) -> None:
        edge.gain = min(self._gain_max, max(0.0, 1.0 + math.fsum(
            modulation.gain - 1.0 for _, modulation in sorted(edge.modulations.items())
        )))
        edge.gate = min((item.gate for item in edge.modulations.values()), default=1.0)
        self._nodes[edge.target].version += 1
        self._retarget(edge.target)

    def _retarget(self, node_id: str) -> None:
        node = self._nodes[node_id]
        node.target = _sigmoid_sum([
            node.baseline, node.input,
            *(self._edges[key].effective_weight * self._edges[key].signal
              for key in self._incoming[node_id]),
        ])

    def _integrate(self, until: float) -> None:
        delta = until - self._time
        if delta:
            for node in self._nodes.values():
                response = -math.expm1(-delta / node.tau)
                node.activation += response * (node.target - node.activation)
                node.activation = min(1.0, max(0.0, node.activation))
        self._time = until

    def _advance(self, now: float) -> None:
        while self._events and self._events[0][0] <= now:
            due, sequence, kind, edge_id, payload = heapq.heappop(self._events)
            self._integrate(due)
            edge = self._edges[edge_id]
            if kind == "signal":
                value, version, expires = payload
                if version <= edge.signal_version:
                    continue
                edge.signal, edge.signal_version, edge.signal_expires_at = value, version, expires
                self._nodes[edge.target].version += 1
                if expires is not None:
                    self._schedule(expires, "signal_expiry", edge_id, version)
                self._retarget(edge.target)
            elif kind == "signal_expiry":
                if edge.signal_version != payload or edge.signal_expires_at != due:
                    continue
                edge.signal, edge.signal_expires_at = 0.0, None
                self._nodes[edge.target].version += 1
                self._retarget(edge.target)
            elif kind == "modulation_expiry":
                modulation = edge.modulations.get(payload)
                if modulation is None or modulation.token != sequence:
                    continue
                del edge.modulations[payload]
                self._remodulate(edge)
        self._integrate(now)

    def advance(self) -> dict:
        self._advance(self._now())
        return self.snapshot()

    def snapshot(self) -> dict:
        """Return detached plain data at the last committed monotonic time."""
        return {
            "time": self._time,
            "nodes": {
                key: {
                    "activation": node.activation, "target": node.target,
                    "input": node.input, "baseline": node.baseline, "tau": node.tau,
                    "version": node.version, "input_version": node.input_version,
                    "observed_at": node.observed_at, "output_version": node.output_version,
                    "emitted_at": node.emitted_at,
                } for key, node in self._nodes.items()
            },
            "edges": {
                key: {
                    "source": edge.source, "target": edge.target,
                    "base_weight": edge.base_weight, "sign": edge.sign,
                    "delay": edge.delay, "gain": edge.gain, "gate": edge.gate,
                    "effective_weight": edge.effective_weight,
                    "signal": edge.signal, "signal_version": edge.signal_version,
                    "signal_expires_at": edge.signal_expires_at,
                    "modulations": {
                        source: {"gain": item.gain, "gate": item.gate, "expires_at": item.expires_at}
                        for source, item in edge.modulations.items()
                    },
                } for key, edge in self._edges.items()
            },
            "pending_events": len(self._events),
        }
