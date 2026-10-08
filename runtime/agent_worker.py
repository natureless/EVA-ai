"""Replaceable execution backend for agent calls.

The default backend uses a bounded thread pool. The interface is deliberately
process-neutral so a process or container implementation can replace it
without changing the cognition pipeline.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import logging
import threading
from typing import Any, Callable, Protocol

from agent_os.orchestrator import AgentOrchestrator
from agents.base_agent import AgentResult

logger = logging.getLogger("eva.agent_worker")


class AgentWorkerBackend(Protocol):
    def execute(
        self,
        agent_name: str,
        task: Any,
        *,
        executor: Any = None,
        token_manager: Any = None,
        token_id: str = "",
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]: ...

    def execute_stream(
        self,
        agent_name: str,
        task: Any,
        on_token: Callable[[str], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]: ...

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]: ...

    def shutdown(self) -> None: ...

    @property
    def stats(self) -> dict[str, Any]: ...

    @property
    def is_idle(self) -> bool: ...


def worker_is_idle(backend: Any) -> bool:
    """Observe actual completion; unknown capacity cannot authorize more work."""
    try:
        idle = getattr(backend, "is_idle", None)
        if isinstance(idle, bool):
            return idle
        stats = backend.stats
        pending = stats.get("pending") if isinstance(stats, dict) else None
        return isinstance(pending, int) and not isinstance(pending, bool) and pending == 0
    except Exception:
        return False


class ThreadAgentWorkerBackend:
    """Default in-process backend with full tool support (timeouts cannot kill threads)."""

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        *,
        max_workers: int = 4,
        timeout_sec: float = 120.0,
    ) -> None:
        self.orchestrator = orchestrator
        self.max_workers = max(1, min(max_workers, 16))
        self.timeout_sec = max(1.0, timeout_sec)
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="eva-agent",
        )
        self._submitted = 0
        self._completed = 0
        self._timed_out = 0
        self._lock = threading.Lock()
        self._in_flight: set[Future[Any]] = set()

    def execute(
        self,
        agent_name: str,
        task: Any,
        *,
        executor: Any = None,
        token_manager: Any = None,
        token_id: str = "",
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]:
        future = self.submit(
            self.orchestrator.execute,
            agent_name,
            task,
            executor=executor,
            token_manager=token_manager,
            token_id=token_id,
        )
        return self._wait(future, agent_name, stop_event)

    def execute_stream(
        self,
        agent_name: str,
        task: Any,
        on_token: Callable[[str], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]:
        future = self.submit(
            self.orchestrator.execute_stream,
            agent_name,
            task,
            on_token,
        )
        return self._wait(future, agent_name, stop_event)

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        future = self._pool.submit(fn, *args, **kwargs)
        with self._lock:
            self._submitted += 1
            self._in_flight.add(future)
        # Register outside the lock: completed futures invoke this immediately.
        future.add_done_callback(self._release_future)
        return future

    def _release_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._in_flight.discard(future)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return not self._in_flight

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "thread",
                "max_workers": self.max_workers,
                "timeout_sec": self.timeout_sec,
                "submitted": self._submitted,
                "completed": self._completed,
                "timed_out": self._timed_out,
                "pending": len(self._in_flight),
            }

    def _wait(
        self,
        future: Future[tuple[AgentResult, int]],
        agent_name: str,
        stop_event: threading.Event | None,
    ) -> tuple[AgentResult, int]:
        poll_interval = min(0.5, self.timeout_sec)
        elapsed = 0.0
        while stop_event is None or not stop_event.is_set():
            try:
                result = future.result(timeout=poll_interval)
                with self._lock:
                    self._completed += 1
                return result
            except FutureTimeoutError:
                elapsed += poll_interval
                if elapsed < self.timeout_sec:
                    continue
                future.cancel()
                with self._lock:
                    self._timed_out += 1
                logger.error("agent %s timed out after %.0fs", agent_name, elapsed)
                return self._error_result(agent_name, "timeout", elapsed)

        future.cancel()
        logger.warning("agent %s cancelled during shutdown", agent_name)
        return self._error_result(agent_name, "cancelled", elapsed)

    @staticmethod
    def _error_result(
        agent_name: str,
        status: str,
        elapsed: float,
    ) -> tuple[AgentResult, int]:
        detail = (
            f"timed out after {elapsed:.0f}s"
            if status == "timeout"
            else "cancelled during shutdown"
        )
        return (
            AgentResult(
                ok=False,
                agent=agent_name,
                content=f"[{agent_name}] {detail}",
                summary=detail,
                meta={"status": status, "elapsed_sec": elapsed},
            ),
            int(elapsed * 1000),
        )


def create_agent_worker_backend(orchestrator: AgentOrchestrator, settings: Any) -> AgentWorkerBackend:
    """Select explicitly; a failed process startup never falls back to threads.

    Process startup is lazy, so constructing Core cannot leak a process pool if
    a later bootstrap stage fails. The first accepted task creates the workers.
    """
    common = dict(max_workers=settings.agent_worker_count,
                  timeout_sec=settings.agent_execution_timeout_sec)
    if settings.agent_worker_backend == "thread":
        return ThreadAgentWorkerBackend(orchestrator, **common)
    if settings.agent_worker_backend == "process":
        from runtime.process_worker import ProcessAgentWorkerBackend

        return ProcessAgentWorkerBackend(
            orchestrator, **common,
            queue_capacity=settings.agent_worker_queue_capacity,
            queue_timeout_sec=settings.agent_worker_queue_timeout_sec,
            max_tasks_per_worker=settings.agent_worker_max_tasks,
            llm_max_retries=settings.llm_max_retries,
        )
    raise ValueError("Unknown agent worker backend")
