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


class ThreadAgentWorkerBackend:
    """Bounded in-process backend used until process/container isolation lands."""

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
        with self._lock:
            self._submitted += 1
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

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
