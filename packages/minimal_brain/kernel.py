"""A small concurrent cognitive runtime over EVA's existing event bus.

The coordinator owns its state. Cheap state nodes have independent deadlines;
blocking cognition and model projections have separate, bounded execution slots.
Only cognition may invoke the supplied stable event processor. Feedback updates
working state without resubmitting an external action.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import asdict, dataclass
import logging
import math
import threading
import time
from typing import Any, Callable

from event.event_bus import EventBus
from event.event_schema import Event
from packages.minimal_brain.goals import GoalLedger, GoalStatus
from packages.minimal_brain.policies import (
    AttentionContext,
    AttentionPolicy,
    Candidate,
    GlobalWorkspace,
    ValuePolicy,
)
from packages.minimal_brain.scheduler import (
    MultiTimescaleScheduler,
    NodePhase,
    PeriodicNode,
)
from packages.minimal_brain.state_graph import StateGraph

logger = logging.getLogger("eva.minimal_brain")


@dataclass(frozen=True)
class KernelConfig:
    queue_capacity: int = 64
    workspace_capacity: int = 5
    body_interval: float = 0.05
    attention_interval: float = 0.1
    goal_interval: float = 0.25
    model_interval: float = 1.0
    poll_interval: float = 0.01
    max_wait_sec: float = 300.0
    history_limit: int = 128
    max_events_per_step: int = 32
    stop_timeout: float = 3.0

    def __post_init__(self) -> None:
        for name in (
            "queue_capacity",
            "workspace_capacity",
            "history_limit",
            "max_events_per_step",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "body_interval",
            "attention_interval",
            "goal_interval",
            "model_interval",
            "poll_interval",
            "max_wait_sec",
            "stop_timeout",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass
class _Pending:
    event: Event
    accepted_at: float
    order: int


class MinimalBrainKernel:
    """Single event owner with independently progressing state and slow cognition.

    ``process_event(event, context)`` owns actual policy checks, LLM/tool execution,
    persistence and reply delivery. It must return the corresponding receipt.
    This kernel never claims it can revoke side effects already dispatched there.

    ``resource_probe`` must be a fast, nonblocking read returning a normalized
    ``pressure`` (optional). ``processor_ready``, ``on_reject`` and ``on_snapshot``
    likewise must not perform blocking I/O: they run on the coordinator. The application observer
    only swaps a cached snapshot; the existing snapshot scheduler writes it.
    ``model_probe`` may block: it runs in its own slot.
    ``step`` supports a fake clock and controlled futures for deterministic tests.
    The same instance cannot be restarted after stop, even if work is still busy.
    """

    def __init__(
        self,
        event_bus: EventBus,
        process_event: Callable[[Event, dict[str, Any]], dict[str, Any]],
        *,
        on_reject: Callable[[Event, str], Any] | None = None,
        resource_probe: Callable[[], dict[str, Any]] | None = None,
        model_probe: Callable[[], dict[str, Any]] | None = None,
        on_snapshot: Callable[[dict[str, Any]], Any] | None = None,
        processor_ready: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        config: KernelConfig | None = None,
        initial_snapshot: dict[str, Any] | None = None,
        executor: Executor | None = None,
        value_policy: ValuePolicy | None = None,
        attention_policy: AttentionPolicy | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.config = config or KernelConfig()
        self._process_event = process_event
        self._on_reject = on_reject
        self._resource_probe = resource_probe
        self._model_probe = model_probe
        self._on_snapshot = on_snapshot
        self._processor_ready = processor_ready
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="eva-brain-cognition"
        )
        self._model_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="eva-brain-models"
        )
        self._active: _Pending | None = None
        self._future: Future | None = None
        self._model_future: Future | None = None
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._seen: OrderedDict[str, str | None] = OrderedDict()
        self._resume_candidates: dict[str, Event] = {}
        self._goals = GoalLedger()
        self._memory: deque[dict[str, Any]] = deque(maxlen=self.config.history_limit)
        self._trace: deque[dict[str, Any]] = deque(maxlen=self.config.history_limit)
        self._workspace = GlobalWorkspace(
            self.config.workspace_capacity,
            value_policy=value_policy,
            attention_policy=attention_policy,
        )
        self._world: dict[str, Any] = {"observations": {}, "last_result": None}
        self._self: dict[str, Any] = {
            "mode": "idle",
            "active_event_id": None,
            "capability_source": "runtime_configuration",
            "active_cancellation_supported": False,
        }
        self._projections: dict[str, Any] = {}
        self._body: dict[str, Any] = {"pressure": 0.0, "probe_status": "not_sampled"}
        self._metrics = {
            "accepted": 0,
            "completed": 0,
            "failed": 0,
            "rejected": 0,
            "duplicates": 0,
            "cancelled": 0,
            "interrupted": 0,
            "probe_errors": 0,
            "snapshot_errors": 0,
            "resumed_pending": 0,
        }
        self._ticks = {
            "body": 0,
            "attention": 0,
            "goals": 0,
            "models": 0,
            "cognition": 0,
            "feedback": 0,
        }
        self._version = 0
        self._last_emitted_version = -1
        self._order = 0
        self._last_time = self._now()
        self._scheduler = MultiTimescaleScheduler()
        self.graph = StateGraph(clock=clock)
        self.graph.add_node("body", tau=0.1, baseline=-6.0)
        self.graph.add_node("attention", tau=0.1, baseline=-2.0)
        self.graph.add_node("goals", tau=0.5, baseline=-2.0)
        self.graph.add_node("cognition", tau=0.25, baseline=-2.0)
        self._risk_edge = self.graph.connect(
            "body", "attention", weight=2.0, edge_id="body_attention"
        )
        self.graph.connect(
            "body", "cognition", weight=1.0, sign=-1, edge_id="body_cognition"
        )
        self.graph.connect("goals", "cognition", weight=2.0, edge_id="goals_cognition")
        for node in (
            PeriodicNode(
                "body",
                self.config.body_interval,
                lambda tick: self._update_body(tick.now),
                phase=NodePhase.BEFORE_GRAPH,
                critical=True,
            ),
            PeriodicNode(
                "goals",
                self.config.goal_interval,
                lambda tick: self._update_goals(tick.now),
                phase=NodePhase.BEFORE_GRAPH,
                critical=True,
            ),
            PeriodicNode(
                "attention",
                self.config.attention_interval,
                lambda tick: self._select_workspace(tick.now),
                critical=True,
            ),
            PeriodicNode(
                "models",
                self.config.model_interval,
                lambda tick: self._schedule_models(),
                ready=lambda: self._model_future is None,
                critical=True,
            ),
        ):
            self._scheduler.register(node, now=self._last_time)
        if initial_snapshot is not None:
            self._restore(initial_snapshot)

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("clock must return finite monotonic time")
        return now

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "minimal_brain",
                "running": self.is_running,
                "closed": self._closed,
                "version": self._version,
                "pending": len(self._pending),
                "worker_count": 1,
                "processor_busy": self._future is not None and not self._future.done(),
                "downstream_busy": not self._execution_capacity_available(),
                "model_probe_busy": self._model_future is not None
                and not self._model_future.done(),
                "node_ticks": dict(self._ticks),
                **self._metrics,
                "nodes": self._scheduler.snapshot(now=self._last_time),
                "total_processed": self._metrics["completed"] + self._metrics["failed"],
                "total_errors": self._metrics["failed"],
            }

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("A stopped kernel cannot be restarted")
            if self.is_running:
                return
            self._thread = threading.Thread(
                target=self._run, name="eva-minimal-brain", daemon=True
            )
            self._thread.start()

    def prepare_request_resume(
        self, lookup: Callable[[str, str | None], Event | None]
    ) -> None:
        """Startup-only reconcile exactly the durable unclaimed snapshot entries."""
        with self._lock:
            if self._thread is not None or self._closed or self._future is not None:
                raise RuntimeError(
                    "cannot reconcile request recovery after kernel start"
                )
            candidates = {}
            for event_id, task_id in self._seen.items():
                event = lookup(event_id, task_id)
                if event is None:
                    continue
                event = Event.model_validate(event.model_dump())
                goal = self._goals[event_id]
                if (
                    event.id != event_id
                    or event.correlation_id != task_id
                    or goal.status is not GoalStatus.INTERRUPTED
                    or goal.source != event.source
                    or goal.event_type != event.type
                    or goal.summary != str(event.payload.get("text", event.type))[:160]
                ):
                    raise ValueError(
                        "durable unclaimed proof conflicts with minimal snapshot"
                    )
                candidates[event_id] = event
            for event_id, event in candidates.items():
                self._seen.pop(event_id)
                self._resume_candidates[event_id] = event
                self._record("unclaimed_resume_prepared", event_id)

    def register_node(self, node: PeriodicNode) -> None:
        """Add a trusted nonblocking node, serialized with all state commits.

        The callback receives actual elapsed time and runs on the coordinator.
        It must not invoke external actions or wait for model/tool I/O. Optional
        failures remain visible in ``stats['nodes']``; critical failures stop the
        coordinator. Registrations are runtime configuration, never restored
        from persisted state or accepted from event payloads.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot register a node on a stopped kernel")
            self._scheduler.register(node, now=self._now())
            self._record("node_registered")

    def unregister_node(self, name: str) -> bool:
        with self._lock:
            if name in {"body", "goals", "attention", "models"}:
                raise ValueError("built-in cognitive nodes cannot be removed")
            removed = self._scheduler.unregister(name)
            if removed:
                self._record("node_unregistered")
            return removed

    def request_stop(self) -> None:
        """Stop admission and dispatch without waiting for slow execution.

        ``stop`` still owns queued rejection, final receipts, and resource
        reclamation. This method can be called repeatedly during shutdown.
        """
        with self._lock:
            self._closed = True
            self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.step()
            except Exception:
                # Do not repeatedly consume new requests after a coordinator fault.
                logger.exception("minimal brain coordinator failed")
                with self._lock:
                    self._closed = True
                    self._stop_event.set()
                    self._reject_pending("kernel_failed")
                    self._record("kernel_failed")
                break
            self._stop_event.wait(self.config.poll_interval)

    def step(self) -> None:
        """Advance ready nodes without waiting for cognition or model I/O."""
        with self._lock:
            now = self._now()
            if now < self._last_time:
                raise ValueError("clock moved backwards")
            self._last_time = now
            self._collect_results(now)
            if self._closed:
                self._emit_snapshot()
                return
            for _ in range(self.config.max_events_per_step):
                event = self.event_bus.consume(timeout=0)
                if event is None:
                    break
                try:
                    self._accept(event, now)
                finally:
                    # Ownership transfers to the bounded kernel queue, not to a
                    # second EventBus consumer. Persistence remains the stable path.
                    self.event_bus.task_done()
            updated = self._scheduler.run_due(now=now, phase=NodePhase.BEFORE_GRAPH)
            self.graph.advance()
            updated += self._scheduler.run_due(now=now, phase=NodePhase.AFTER_GRAPH)
            if updated:
                # Includes scheduler health changes even when attention was not due.
                self._version += 1
            self._dispatch(now)
            self._trim_history()
            self._emit_snapshot()

    def _accept(self, event: Event, now: float) -> None:
        if event.id in self._seen:
            self._metrics["duplicates"] += 1
            # A reused ID with another waiter must not leave that waiter hanging.
            if event.correlation_id != self._seen[event.id]:
                self._reject(event, "duplicate_event_id")
            return
        if len(self._pending) >= self.config.queue_capacity:
            self._reject(event, "cognitive_queue_full")
            return
        event = event.model_copy(deep=True)
        restored = self._resume_candidates.get(event.id)
        if restored is not None:
            if restored.model_dump(mode="json") != event.model_dump(mode="json"):
                self._reject(event, "resume_event_mismatch")
                return
            goal = self._goals.resume_unclaimed(event)
            self._resume_candidates.pop(event.id)
            self._metrics["resumed_pending"] += 1
        else:
            goal = self._goals.accept(event)
        self._order += 1
        self._seen[event.id] = event.correlation_id
        self._pending[event.id] = _Pending(event, now, self._order)
        current = self._world["observations"].get(event.type)
        observed_at = event.timestamp.isoformat()
        # Compare datetimes, not arrival order or differently-offset ISO strings.
        if (
            current is None
            or event.timestamp.timestamp() >= current["observed_timestamp"]
        ):
            self._world["observations"][event.type] = {
                "event_id": event.id,
                "observed_at": observed_at,
                "observed_timestamp": event.timestamp.timestamp(),
                "summary": goal.summary,
                "epistemic_status": "user_statement"
                if event.type == "user_message"
                else "observation",
                "source": event.source,
            }
        self._metrics["accepted"] += 1
        self._record("event_accepted", event.id)
        self._scheduler.wake("attention", now=now)

    def _schedule_models(self) -> None:
        if self._model_probe is not None:
            self._model_future = self._model_executor.submit(self._model_probe)

    def _update_body(self, now: float) -> None:
        self._ticks["body"] += 1
        measurements: dict[str, Any] = {}
        status = "local_queue_only"
        if self._resource_probe is not None:
            try:
                measurements = dict(self._resource_probe())
                supplied = measurements.get("pressure", 0.0)
                if (
                    isinstance(supplied, bool)
                    or not isinstance(supplied, (float, int))
                    or not math.isfinite(supplied)
                    or not 0 <= supplied <= 1
                ):
                    raise ValueError("resource pressure must be in [0, 1]")
                status = "sampled"
            except Exception:
                self._metrics["probe_errors"] += 1
                measurements = {}
                status = "unavailable"
        queue_pressure = len(self._pending) / self.config.queue_capacity
        pressure = max(float(measurements.get("pressure", 0.0)), queue_pressure)
        self._body = {
            "pressure": pressure,
            "queue_pressure": queue_pressure,
            "probe_status": status,
            "measurements": measurements,
        }
        version = self._ticks["body"]
        self.graph.set_input("body", 12.0 * pressure, source_version=version)
        self.graph.publish(
            "body",
            value=pressure,
            source_version=version,
            ttl=2 * self.config.body_interval,
        )
        self.graph.modulate(
            self._risk_edge,
            gain=1.0 + 2.0 * pressure,
            ttl=2 * self.config.body_interval,
            source="resource_pressure",
        )

    def _update_goals(self, now: float) -> None:
        self._ticks["goals"] += 1
        for event_id, pending in list(self._pending.items()):
            if now - pending.accepted_at >= self.config.max_wait_sec:
                del self._pending[event_id]
                self._finish_goal(event_id, "expired")
                self._reject(pending.event, "cognitive_queue_timeout")
        demand = min(1.0, len(self._pending) / max(1, self.config.workspace_capacity))
        self.graph.set_input("goals", demand * 4, source_version=self._ticks["goals"])
        self.graph.publish(
            "goals",
            value=demand,
            source_version=self._ticks["goals"],
            ttl=2 * self.config.goal_interval,
        )

    def _select_workspace(self, now: float) -> None:
        self._ticks["attention"] += 1
        attention = self.graph.snapshot()["nodes"]["attention"]["activation"]
        candidates = tuple(
            Candidate(
                event_id=item.event.id,
                event_type=item.event.type,
                goal_version=self._goals[item.event.id].version,
                summary=self._goals[item.event.id].summary,
                accepted_at=item.accepted_at,
                order=item.order,
            )
            for item in self._pending.values()
        )
        self._workspace.select(
            candidates, AttentionContext(now, self._body["pressure"], attention)
        )
        if self._active is not None:
            self._self["active_event_id"] = self._active.event.id
        self._version += 1

    def _dispatch(self, now: float) -> None:
        if self._closed or self._future is not None or self._workspace.first is None:
            return
        # A stable receipt can arrive before a timed-out thread actually exits.
        # Keep its physical capacity reserved instead of overlapping new actions.
        if not self._execution_capacity_available():
            return
        chosen = self._workspace.first
        event_id = chosen.event_id
        pending = self._pending.get(event_id)
        goal = self._goals.get(event_id)
        if pending is None or goal is None or chosen.goal_version != goal.version:
            self._select_workspace(now)
            return
        if now - pending.accepted_at >= self.config.max_wait_sec:
            self._update_goals(now)
            self._select_workspace(now)
            return
        del self._pending[event_id]
        self._active = pending
        self._finish_goal(event_id, "running", expected_version=chosen.goal_version)
        self._self.update(mode="cognition", active_event_id=event_id)
        self._ticks["cognition"] += 1
        context = {
            "schema_version": 1,
            "state_version": self._version,
            "goal": self._goals[event_id].snapshot(),
            "body": deepcopy(self._body),
            "workspace": self._workspace.snapshot(),
            "world": deepcopy(self._world),
            "self": deepcopy(self._self),
            "recent_feedback": deepcopy(list(self._memory)[-5:]),
            "models": deepcopy(self._projections),
            "epistemic_note": "Runtime observations and prior assistant outputs are not verified facts.",
        }
        try:
            self._future = self._executor.submit(
                self._process_event, pending.event, context
            )
        except Exception:
            self._active = None
            self._finish_goal(event_id, "failed")
            self._self.update(mode="idle", active_event_id=None)
            self._metrics["failed"] += 1
            self._reject(pending.event, "cognition_dispatch_failed")
        self._record("cognition_dispatched", event_id)
        self._select_workspace(now)

    def _collect_results(self, now: float) -> None:
        if self._model_future is not None and self._model_future.done():
            future, self._model_future = self._model_future, None
            try:
                result = future.result()
                if not isinstance(result, dict):
                    raise ValueError("model probe must return an object")
                self._projections = deepcopy(result)
                self._ticks["models"] += 1
                self._record("models_projected")
            except Exception:
                self._metrics["probe_errors"] += 1
        if self._future is None or not self._future.done():
            return
        future, pending = self._future, self._active
        self._future = None
        self._active = None
        if pending is None:
            raise RuntimeError("processor completed without an owning event")
        try:
            receipt = future.result()
            if not isinstance(receipt, dict) or not isinstance(receipt.get("ok"), bool):
                raise ValueError("processor must return a receipt with a boolean ok")
        except Exception:
            logger.exception("minimal brain event processor failed")
            receipt = {"ok": False, "error": "cognition_failed", "reply": ""}
            self._reject(pending.event, "cognition_failed")
        status = "completed" if receipt["ok"] else "failed"
        self._finish_goal(pending.event.id, status)
        self._metrics[status] += 1
        feedback = {
            "event_id": pending.event.id,
            "correlation_id": pending.event.correlation_id,
            "ok": receipt["ok"],
            "error": receipt.get("error"),
            "summary": str(receipt.get("reply", ""))[:240],
            "source": str(receipt.get("selected_agent", "event_processor")),
            "epistemic_status": "assistant_inference",
            "result_scope": "event_handling_receipt",
        }
        self._memory.append(feedback)
        self._world["last_result"] = deepcopy(feedback)
        self._self.update(mode="idle", active_event_id=None)
        self._ticks["feedback"] += 1
        self._record("feedback_integrated", pending.event.id)
        # Feedback affects the next choice, but never republishes the action.
        self._scheduler.wake("attention", now=now)

    def _finish_goal(
        self, event_id: str, status: str, *, expected_version: int | None = None
    ) -> None:
        self._goals.transition(event_id, status, expected_version=expected_version)
        self._version += 1

    def _reject(self, event: Event, reason: str) -> None:
        self._metrics["rejected"] += 1
        self._record(reason, event.id)
        if self._on_reject is not None:
            try:
                self._on_reject(event, reason)
            except Exception:
                logger.exception("failed to deliver cognitive rejection")

    def cancel(self, event_id: str) -> bool:
        """Cancel only work that has not crossed the stable execution boundary."""
        with self._lock:
            pending = self._pending.pop(event_id, None)
            if pending is None:
                return False
            self._finish_goal(event_id, "cancelled")
            self._metrics["cancelled"] += 1
            self._reject(pending.event, "cancelled")
            self._select_workspace(self._now())
            self._emit_snapshot()
            return True

    def _reject_pending(self, reason: str) -> None:
        for event_id, pending in list(self._pending.items()):
            del self._pending[event_id]
            self._finish_goal(event_id, "interrupted")
            self._metrics["interrupted"] += 1
            self._reject(pending.event, reason)
        self._workspace.clear()

    def stop(self, timeout: float | None = None) -> bool:
        """Stop accepting work; report whether all actual work has stopped.

        A timeout cannot terminate a running Python callback. The owner must keep
        its dependencies open and retry stop after that work exits.
        """
        timeout = self.config.stop_timeout if timeout is None else timeout
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("stop timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
            self._stop_event.set()
            self._reject_pending("kernel_stopped")
            # Producers are stopped by the application before this call. Bound
            # the drain to the currently queued batch even for standalone use.
            for _ in range(self.event_bus.size()):
                event = self.event_bus.consume(timeout=0)
                if event is None:
                    break
                try:
                    if (
                        event.id not in self._seen
                        or event.correlation_id != self._seen[event.id]
                    ):
                        self._reject(event, "kernel_stopped")
                finally:
                    self.event_bus.task_done()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            futures = [f for f in (self._future, self._model_future) if f is not None]
        if futures:
            wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._collect_results(self._last_time)
            stopped = (
                not self.is_running
                and self._future is None
                and self._model_future is None
                and self._execution_capacity_available()
            )
            self._self["mode"] = "stopped" if stopped else "stopping"
            self._record("kernel_stopped" if stopped else "kernel_stopping")
            self._emit_snapshot()
            if stopped:
                if self._owns_executor:
                    self._executor.shutdown(wait=True)
                self._model_executor.shutdown(wait=True)
            return stopped

    def _execution_capacity_available(self) -> bool:
        if self._processor_ready is None:
            return True
        try:
            return self._processor_ready() is True
        except Exception:
            # Unknown capacity cannot safely admit another side-effecting task.
            return False

    def _record(self, kind: str, event_id: str | None = None) -> None:
        self._version += 1
        self._trace.append(
            {"version": self._version, "kind": kind, "event_id": event_id}
        )

    def _trim_history(self) -> None:
        protected = set(self._pending) | set(self._resume_candidates)
        if self._active is not None:
            protected.add(self._active.event.id)
        for key in list(self._seen):
            if len(self._seen) <= self.config.history_limit + len(protected):
                break
            if key not in protected:
                del self._seen[key]
        self._goals.trim(history_limit=self.config.history_limit, protected=protected)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(
                {
                    "schema_version": 1,
                    "version": self._version,
                    "config": asdict(self.config),
                    "graph": self.graph.snapshot(),
                    "body": self._body,
                    "workspace": self._workspace.snapshot(),
                    "goals": self._goals.snapshot(),
                    "world": self._world,
                    "self": self._self,
                    "memory": list(self._memory),
                    "models": self._projections,
                    "trace": list(self._trace),
                    "seen": list(self._seen.items()),
                    "stats": self.stats,
                }
            )

    def _emit_snapshot(self) -> None:
        if (
            self._on_snapshot is not None
            and self._version != self._last_emitted_version
        ):
            try:
                self._on_snapshot(self.snapshot())
                self._last_emitted_version = self._version
            except Exception:
                self._metrics["snapshot_errors"] += 1
                logger.exception("minimal brain snapshot observer failed")

    def _restore(self, saved: dict[str, Any]) -> None:
        if not isinstance(saved, dict) or saved.get("schema_version") != 1:
            raise ValueError("unsupported minimal brain snapshot")
        version = saved.get("version", 0)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("invalid minimal brain snapshot version")
        self._version = version
        limit = self.config.history_limit + self.config.queue_capacity + 1
        for raw in saved.get("goals", [])[-limit:]:
            goal, interrupted = self._goals.restore(raw)
            if interrupted:
                self._metrics["interrupted"] += 1
            self._seen[goal.event_id] = goal.correlation_id
        for item in saved.get("memory", [])[-self.config.history_limit :]:
            if not isinstance(item, dict):
                raise ValueError("invalid saved memory")
            self._memory.append(deepcopy(item))
        world = saved.get("world", {})
        if isinstance(world, dict) and isinstance(world.get("observations"), dict):
            self._world = deepcopy(world)
        self._record("snapshot_restored")
        # Runtime clocks, active futures, graph gains and queued external actions
        # are intentionally reconstructed, never resumed from a stale snapshot.
