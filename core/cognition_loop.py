"""FIFO event scheduling and consumer-thread lifecycle."""

import logging
import math
import threading
import time
from typing import Any

from agent_os.orchestrator import AgentOrchestrator
from agent_os.router import AgentRouter
from core.context_builder import ContextBuilder
from core.event_processor import EventProcessor
from core.planner import Planner
from core.policy_engine import PolicyEngine
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from event.event_bus import EventBus
from event.event_schema import Event
from memory.memory_api import MemoryAPI
from memory.memory_governor import MemoryGovernor
from memory.tiered_store import TieredMemoryManager
from persona.self_model_store import SelfModelStore
from runtime.result_registry import ResultRegistry
from runtime.agent_worker import AgentWorkerBackend
from world.world_model import WorldModelGraph


class CognitionLoop:
    """Legacy FIFO scheduler around a separately injectable EventProcessor.

    This class owns only EventBus consumption, acknowledgement, and worker
    lifetime. Existing dependency-based construction remains supported; new
    composition roots construct EventProcessor once and pass processor=.
    """

    def __init__(
        self,
        event_bus: EventBus,
        memory_api: MemoryAPI | None = None,
        memory_governor: MemoryGovernor | None = None,
        planner: Planner | None = None,
        agent_router: AgentRouter | None = None,
        orchestrator: AgentOrchestrator | None = None,
        result_registry: ResultRegistry | None = None,
        proactive_engine: ProactiveEngine | None = None,
        proactive_state: dict[str, Any] | None = None,
        world_model: WorldModelGraph | None = None,
        system_state: dict[str, Any] | None = None,
        poll_timeout_sec: float = 0.5,
        result_ttl_sec: float = 60.0,
        context_builder: ContextBuilder | None = None,
        prediction_tracker: PredictionTracker | None = None,
        self_model_store: SelfModelStore | None = None,
        self_model: dict[str, Any] | None = None,
        policy_engine: PolicyEngine | None = None,
        tiered_memory: TieredMemoryManager | None = None,
        executors: dict[str, Any] | None = None,
        ws_manager: Any = None,
        worker_count: int = 1,
        agent_worker_backend: AgentWorkerBackend | None = None,
        *,
        processor: EventProcessor | None = None,
    ) -> None:
        if processor is None:
            dependencies = {
                "memory_api": memory_api,
                "planner": planner,
                "agent_router": agent_router,
                "orchestrator": orchestrator,
                "result_registry": result_registry,
                "proactive_engine": proactive_engine,
                "proactive_state": proactive_state,
                "world_model": world_model,
                "system_state": system_state,
            }
            missing = [name for name, value in dependencies.items() if value is None]
            if missing:
                raise TypeError("Missing processor dependencies: " + ", ".join(missing))
            processor = EventProcessor(
                event_bus=event_bus,
                memory_api=memory_api,
                memory_governor=memory_governor,
                planner=planner,
                agent_router=agent_router,
                orchestrator=orchestrator,
                result_registry=result_registry,
                proactive_engine=proactive_engine,
                proactive_state=proactive_state,
                world_model=world_model,
                system_state=system_state,
                result_ttl_sec=result_ttl_sec,
                context_builder=context_builder,
                prediction_tracker=prediction_tracker,
                self_model_store=self_model_store,
                self_model=self_model,
                policy_engine=policy_engine,
                tiered_memory=tiered_memory,
                executors=executors,
                ws_manager=ws_manager,
                agent_worker_backend=agent_worker_backend,
            )
        elif processor.event_bus is not event_bus:
            raise ValueError("Scheduler and processor must share one EventBus")
        self.processor = processor
        self.event_bus = event_bus
        self.poll_timeout_sec = poll_timeout_sec
        self._worker_count = max(1, min(worker_count, 8))
        self._threads: list[threading.Thread] = []
        self._lifecycle_lock = threading.RLock()
        self._operation_lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        """Keep historical read access while ownership moves to the service."""
        processor = self.__dict__.get("processor")
        if processor is not None:
            return getattr(processor, name)
        raise AttributeError(name)

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return (
                not self.processor.stop_requested
                and len(self._threads) == self._worker_count
                and all(thread.is_alive() for thread in self._threads)
            )

    def start(self) -> None:
        with self._operation_lock, self._lifecycle_lock:
            if self.is_running:
                return
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("Previous cognition workers have not stopped")
            self.processor.reset_stop()
            self._threads = []
            try:
                for index in range(self._worker_count):
                    thread = threading.Thread(
                        target=self._run_forever,
                        daemon=True,
                        name=f"cognition-loop-{index}"
                        if self._worker_count > 1
                        else "cognition-loop",
                    )
                    thread.start()
                    self._threads.append(thread)
            except Exception:
                self.processor.request_stop()
                raise
        logging.getLogger("eva.cognition_loop").info(
            "cognition loop started with %d worker(s), agent_backend=%s",
            self._worker_count,
            self.processor.stats["agent_worker"].get("backend", "unknown"),
        )

    def request_stop(self) -> None:
        self.processor.request_stop()

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop consumption within one deadline; retain workers still alive.

        A cancelled/expired caller may leave a real backend thread running.
        Such work keeps this return value false and keeps owned resources open.
        A later call can finish shutdown after the real work has returned.
        """
        if timeout < 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be a finite non-negative number")
        deadline = time.monotonic() + timeout
        with self._operation_lock:
            self.request_stop()
            with self._lifecycle_lock:
                threads = tuple(self._threads)
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
            with self._lifecycle_lock:
                self._threads = [thread for thread in self._threads if thread.is_alive()]
                if self._threads:
                    return False
            while not self.processor.is_idle:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.01, remaining))
            return self.processor.shutdown_owned_backend()

    @property
    def stats(self) -> dict[str, Any]:
        return {"worker_count": self._worker_count, **self.processor.stats}

    def process_event(
        self,
        event: Event,
        *,
        cognitive_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility facade; new schedulers inject EventProcessor directly."""
        return self.processor.process_event(event, cognitive_context=cognitive_context)

    def reject_event(self, event: Event, reason: str) -> dict[str, Any]:
        return self.processor.reject_event(event, reason)

    def _run_forever(self) -> None:
        while not self.processor.stop_requested:
            event = self.event_bus.consume(timeout=self.poll_timeout_sec)
            if event is None:
                continue
            try:
                self.process_event(event)
            finally:
                self.event_bus.task_done()
