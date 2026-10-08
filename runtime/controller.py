"""One lifecycle owner for the selected cognition consumer and its dependencies."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Callable, Protocol

from event.event_bus import EventBus
from event.event_schema import Event
from runtime.agent_worker import worker_is_idle


class CognitiveConsumer(Protocol):
    def start(self) -> None: ...
    def request_stop(self) -> None: ...
    def stop(self, timeout: float = 3.0) -> bool: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def stats(self) -> dict[str, Any]: ...


class EventProcessingService(Protocol):
    def process_event(self, event: Event, *, cognitive_context: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def reject_event(self, event: Event, reason: str) -> dict[str, Any]: ...
    def request_stop(self) -> None: ...

    @property
    def stats(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ShutdownStep:
    name: str
    action: Callable[[], Any]


class RuntimeController:
    """Bind exactly one consumer; close ingress before stopping dependencies.

    Successful shutdown steps run once. A failed step preserves its downstream
    resources and can be retried. Timeout bounds consumer waiting, not arbitrary
    storage finalizers. External effects are neither cancelled nor replayed here.
    """

    def __init__(
        self,
        *,
        mode: str,
        consumer: CognitiveConsumer,
        processor: EventProcessingService,
        event_bus: EventBus,
        worker_backend: Any,
        system_state: dict[str, Any],
        producers: tuple[ShutdownStep, ...] = (),
        finalizers: tuple[ShutdownStep, ...] = (),
    ) -> None:
        names = [step.name for step in (*producers, *finalizers)]
        if len(names) != len(set(names)):
            raise ValueError("shutdown step names must be unique")
        self.mode = mode
        self.consumer = consumer
        self.processor = processor
        self.event_bus = event_bus
        self.worker_backend = worker_backend
        self.system_state = system_state
        self._producers = producers
        self._finalizers = finalizers
        self._phase = "created"
        self._lock = threading.RLock()
        self._operation = threading.RLock()
        self._completed_steps: set[str] = set()
        self._errors: dict[str, str] = {}
        self._rejections: deque[Event] = deque()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._phase == "running" and self.consumer.is_running is True

    @property
    def accepting(self) -> bool:
        return self.is_running and self.event_bus.accepting

    @property
    def stats(self) -> dict[str, Any]:
        return self.snapshot()

    def start(self) -> None:
        with self._operation:
            with self._lock:
                if self._phase == "running":
                    if self.consumer.is_running:
                        return
                    raise RuntimeError("failed runtime requires a new instance")
                if self._phase != "created" or not self.event_bus.accepting:
                    raise RuntimeError("runtime cannot be restarted after shutdown")
                self._phase = "starting"
            try:
                self.consumer.start()
                if self.consumer.is_running is not True:
                    raise RuntimeError("cognition consumer did not start")
            except Exception as exc:
                self.event_bus.close()
                with self._lock:
                    self._phase = "failed"
                    self._errors["start"] = str(exc)
                raise
            with self._lock:
                self._phase = "running"
                self.system_state["runtime_mode"] = self.mode
                self.system_state["loop_ready"] = True

    def request_stop(self) -> None:
        """Close ingress immediately; stop() performs the ordered cleanup."""
        with self._operation:
            self.event_bus.close()
            with self._lock:
                if self._phase == "stopped":
                    return
                self._phase = "stopping"
                self.system_state.update(ready=False, loop_ready=False, shutdown_status="incomplete")
            # Neither callback may wait for a slow model or tool to finish.
            self.consumer.request_stop()
            self.processor.request_stop()

    def stop(self, timeout: float = 3.0) -> bool:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("stop timeout must be finite and nonnegative")
        with self._operation:
            if self._phase == "stopped":
                return True
            deadline = time.monotonic() + timeout
            try:
                self.request_stop()
            except Exception as exc:
                return self._failed("request_stop", exc)
            # Keep all dependency references alive if a producer failed to stop.
            for step in self._producers:
                if not self._run_step(step):
                    return False
            try:
                stopped = self.consumer.stop(timeout=max(0.0, deadline - time.monotonic()))
            except Exception as exc:
                return self._failed("consumer", exc)
            if stopped is not True or not worker_is_idle(self.worker_backend):
                return False
            self._rejections.extend(self.event_bus.drain())
            while self._rejections:
                try:
                    self.processor.reject_event(self._rejections[0], "runtime_stopped")
                except Exception as exc:
                    return self._failed("reject_pending", exc)
                self._rejections.popleft()
            for step in self._finalizers:
                if not self._run_step(step):
                    return False
            with self._lock:
                self._errors.clear()
                self._phase = "stopped"
                self.system_state.update(
                    ready=False, loop_ready=False, scheduler_running=False,
                    shutdown_status="complete",
                )
            return True

    def _run_step(self, step: ShutdownStep) -> bool:
        with self._lock:
            if step.name in self._completed_steps:
                return True
        try:
            if step.action() is False:
                raise RuntimeError("component still has active work")
        except Exception as exc:
            return self._failed(step.name, exc)
        with self._lock:
            self._completed_steps.add(step.name)
            self._errors.pop(step.name, None)
        return True

    def _failed(self, name: str, exc: Exception) -> bool:
        with self._lock:
            self._phase = "failed"
            self._errors[name] = str(exc)
            self.system_state.update(ready=False, loop_ready=False, shutdown_status="incomplete")
        return False

    def snapshot(self) -> dict[str, Any]:
        # No model/store I/O: only public, bounded runtime observations.
        with self._lock:
            phase = self._phase
            if phase == "running" and not self.consumer.is_running:
                phase = "failed"
            return deepcopy({
                "schema_version": 1,
                "mode": self.mode,
                "consumer_type": f"{type(self.consumer).__module__}.{type(self.consumer).__qualname__}",
                "processor_type": f"{type(self.processor).__module__}.{type(self.processor).__qualname__}",
                "phase": phase,
                "accepting_events": phase == "running" and self.event_bus.accepting,
                "consumer_running": self.consumer.is_running,
                "execution_idle": worker_is_idle(self.worker_backend),
                "event_bus": self.event_bus.stats(),
                "consumer": self.consumer.stats,
                "processor": self.processor.stats,
                "worker": self.worker_backend.stats,
                "shutdown": {
                    "completed_steps": sorted(self._completed_steps),
                    "errors": dict(self._errors),
                    "pending_rejections": len(self._rejections),
                },
            })
