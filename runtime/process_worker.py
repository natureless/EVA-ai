"""Bounded, spawn-only agent pool with disposable workers and JSON IPC.

Only built-in text agents run remotely in this phase. Core retains executor
gates, audit and response review. No automatic retry of dispatched tasks.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import math
import multiprocessing
from multiprocessing.connection import Connection
import queue
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agents.base_agent import AgentResult, AgentTask
from runtime.worker_main import run_worker
from runtime.worker_protocol import (
    MAX_MESSAGE_BYTES,
    TaskOutcome,
    WorkerHeartbeat,
    WorkerRequest,
    WorkerResponse,
    decode_message,
    encode_message,
)


@dataclass
class _Worker:
    worker_id: str
    process: Any
    connection: Connection
    incoming: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=4))
    outgoing: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    stopped: threading.Event = field(default_factory=threading.Event)
    broken: threading.Event = field(default_factory=threading.Event)
    fault: str = "worker_crash"
    busy: bool = False
    tasks: int = 0
    heartbeat_at: float = field(default_factory=time.monotonic)
    threads: list[threading.Thread] = field(default_factory=list)

    def start_io(self) -> None:
        # Reader continuously drains heartbeats. A blocked writer cannot hold
        # the calling thread beyond its deadline; terminating the child breaks IPC.
        for target in (self._read, self._write):
            thread = threading.Thread(target=target, name=f"eva-ipc-{self.worker_id}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def _read(self) -> None:
        try:
            while not self.stopped.is_set():
                message = decode_message(self.connection.recv_bytes(MAX_MESSAGE_BYTES))
                if message.get("type") == "heartbeat":
                    pulse = WorkerHeartbeat.model_validate(message)
                    if pulse.worker_id != self.worker_id or pulse.pid != self.process.pid:
                        raise ValueError("Heartbeat identity mismatch")
                    self.heartbeat_at = time.monotonic()
                else:
                    self.incoming.put_nowait(message)
        except (EOFError, BrokenPipeError):
            self.broken.set()
        except Exception:
            self.fault = "protocol_error"
            self.broken.set()

    def _write(self) -> None:
        try:
            while not self.stopped.is_set():
                try:
                    data = self.outgoing.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.connection.send_bytes(data)
        except (OSError, EOFError):
            self.broken.set()


class ProcessAgentWorkerBackend:
    def __init__(
        self,
        orchestrator: AgentOrchestrator | None = None,
        *,
        max_workers: int = 4,
        timeout_sec: float = 120.0,
        queue_capacity: int = 16,
        queue_timeout_sec: float = 5.0,
        startup_timeout_sec: float = 15.0,
        heartbeat_timeout_sec: float = 10.0,
        max_tasks_per_worker: int = 100,
        shutdown_grace_sec: float = 2.0,
        llm_max_retries: int = 2,
    ) -> None:
        if not 1 <= max_workers <= 16 or not 0 <= queue_capacity <= 256:
            raise ValueError("Invalid worker count or queue capacity")
        for value in (timeout_sec, queue_timeout_sec, startup_timeout_sec, heartbeat_timeout_sec):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Worker timeouts must be finite and positive")
        if (
            max_tasks_per_worker < 1
            or not math.isfinite(shutdown_grace_sec)
            or shutdown_grace_sec < 0
        ):
            raise ValueError("Invalid recycling or shutdown setting")
        self.orchestrator = orchestrator or AgentOrchestrator(AgentRegistry())
        self.max_workers = max_workers
        self.timeout_sec = timeout_sec
        self.queue_capacity = queue_capacity
        self.queue_timeout_sec = queue_timeout_sec
        self.startup_timeout_sec = startup_timeout_sec
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.max_tasks_per_worker = max_tasks_per_worker
        self.shutdown_grace_sec = shutdown_grace_sec
        self.llm_max_retries = llm_max_retries
        self._context = multiprocessing.get_context("spawn")
        self._worker_target = run_worker
        self._workers: dict[str, _Worker] = {}
        self._condition = threading.Condition()
        self._lifecycle = threading.RLock()
        self._started = False
        self._closing = False
        self._abort = threading.Event()
        self._admission = threading.BoundedSemaphore(max_workers + queue_capacity)
        self._aux_admission = threading.BoundedSemaphore(max_workers + queue_capacity)
        self._aux = ThreadPoolExecutor(
            max_workers=min(max_workers, 2), thread_name_prefix="eva-core-aux"
        )
        self._aux_futures: set[Future[Any]] = set()
        self._counts = dict(
            submitted=0,
            completed=0,
            timed_out=0,
            queue_timed_out=0,
            rejected=0,
            crashed=0,
            restarted=0,
            cancelled=0,
        )

    @property
    def active_workers(self) -> dict[str, _Worker]:
        with self._condition:
            return dict(self._workers)

    def start(self) -> None:
        with self._lifecycle:
            if self._closing:
                raise RuntimeError("Worker backend is shut down")
            if self._started:
                return
            try:
                for _ in range(self.max_workers):
                    worker = self._spawn()
                    with self._condition:
                        self._workers[worker.worker_id] = worker
                self._started = True
            except Exception:
                # A partial pool must not leak children when startup fails.
                self.shutdown()
                raise

    def _spawn(self) -> _Worker:
        parent, child = self._context.Pipe(duplex=True)
        worker_id = uuid4().hex
        process = self._context.Process(
            target=self._worker_target,
            args=(child, worker_id, self.llm_max_retries),
            name=f"eva-agent-{worker_id[:8]}",
            daemon=True,
        )
        worker = _Worker(worker_id, process, parent)
        try:
            process.start()
            child.close()
            worker.start_io()
            deadline = time.monotonic() + self.startup_timeout_sec
            while time.monotonic() < deadline:
                if self._abort.is_set() or worker.broken.is_set():
                    raise RuntimeError("Worker failed during startup")
                try:
                    ready = worker.incoming.get(
                        timeout=min(0.05, max(0.001, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if (
                    ready.get("type") != "ready"
                    or ready.get("worker_id") != worker_id
                    or ready.get("pid") != process.pid
                ):
                    raise RuntimeError("Invalid worker startup message")
                return worker
            raise RuntimeError("Worker startup timed out")
        except Exception:
            child.close()
            self._retire(worker)
            raise

    @staticmethod
    def _retire(worker: _Worker, graceful: bool = False) -> None:
        if worker.process.pid is not None:
            if graceful and worker.process.is_alive():
                try:
                    worker.outgoing.put_nowait(encode_message({"type": "stop"}))
                except queue.Full:
                    pass
                worker.process.join(timeout=0.3)
            if worker.process.is_alive():
                worker.process.terminate()
                worker.process.join(timeout=1.0)
            if worker.process.is_alive():
                worker.process.kill()
                worker.process.join(timeout=1.0)
        worker.stopped.set()
        worker.connection.close()
        for thread in worker.threads:
            thread.join(timeout=0.2)

    def execute(
        self,
        agent_name: str | WorkerRequest,
        task: AgentTask | None = None,
        *,
        executor: Any = None,
        token_manager: Any = None,
        token_id: str = "",
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]:
        began = time.monotonic()
        try:
            if isinstance(agent_name, WorkerRequest):
                # Revalidate instances too: Pydantic objects can be mutated after construction.
                request = WorkerRequest.model_validate(agent_name.model_dump())
            else:
                if task is None:
                    raise ValueError("AgentTask is required")
                request = WorkerRequest(
                    agent_name=agent_name,
                    agent_task_kind=task.kind,
                    payload=task.payload,
                    deadline_sec=self.timeout_sec,
                    **task.trace_context,
                )
            frame = encode_message({"type": "task", **request.model_dump(mode="json")})
        except (TypeError, ValueError, RecursionError):
            name = agent_name.agent_name if isinstance(agent_name, WorkerRequest) else agent_name
            return self._failure(
                name or "unknown", TaskOutcome.PROTOCOL_ERROR, "invalid_request", began
            ), 0
        result, _ = self.orchestrator.execute_delegated(
            request.agent_name,
            AgentTask(
                request.agent_task_kind,
                request.payload,
                {
                    key: getattr(request, key)
                    for key in ("task_id", "trace_id", "correlation_id", "causation_id", "loop_id")
                },
            ),
            lambda _: self._dispatch(request, frame, stop_event),
            executor=executor,
            token_manager=token_manager,
            token_id=token_id,
        )
        return result, max(1, int((time.monotonic() - began) * 1000))

    def execute_raw(self, data: dict[str, Any]) -> tuple[AgentResult, int]:
        try:
            request = WorkerRequest.model_validate(data)
        except (ValueError, TypeError, RecursionError):
            return self._failure(
                "unknown", TaskOutcome.PROTOCOL_ERROR, "invalid_request", time.monotonic()
            ), 0
        return self.execute(request)

    def execute_stream(
        self,
        agent_name: str | WorkerRequest,
        task: AgentTask | Callable | None = None,
        on_token: Callable[[str], None] | None = None,
        *,
        stop_event: threading.Event | None = None,
    ) -> tuple[AgentResult, int]:
        # Support both the Core interface and the serialized-request interface.
        if isinstance(agent_name, WorkerRequest):
            callback = on_token or task
            request = agent_name.model_copy(update={"stream": True})
        else:
            callback = on_token
            if not isinstance(task, AgentTask):
                raise TypeError("AgentTask is required")
            request = WorkerRequest(
                agent_name=agent_name,
                agent_task_kind=task.kind,
                payload=task.payload,
                deadline_sec=self.timeout_sec,
                stream=True,
                **task.trace_context,
            )
        if not callable(callback):
            raise TypeError("on_token callback is required")
        if request.agent_name != "chat_agent":
            result = self._failure(
                request.agent_name,
                TaskOutcome.POLICY_DENIED,
                "streaming_requires_chat_agent",
                time.monotonic(),
            )
            callback(result.content)
            return result, 0
        result, duration = self.execute(request, stop_event=stop_event)
        callback(result.content)  # Only reviewed final text reaches clients.
        return result, duration

    def _dispatch(
        self, request: WorkerRequest, frame: bytes, stop: threading.Event | None
    ) -> AgentResult:
        began = time.monotonic()
        with self._condition:
            self._counts["submitted"] += 1
        if not self._admission.acquire(blocking=False):
            with self._condition:
                self._counts["rejected"] += 1
                self._counts["completed"] += 1
            return self._failure(
                request.agent_name, TaskOutcome.RESOURCE_EXHAUSTED, "queue_full", began, request
            )
        worker = None
        recycle = False
        try:
            if self._cancelled(stop) or self._closing:
                return self._failure(
                    request.agent_name, TaskOutcome.CANCELLED, "cancelled", began, request
                )
            self.start()
            queue_deadline = time.monotonic() + self.queue_timeout_sec
            with self._condition:
                while worker is None:
                    if self._cancelled(stop) or self._closing:
                        return self._failure(
                            request.agent_name, TaskOutcome.CANCELLED, "cancelled", began, request
                        )
                    worker = next((w for w in self._workers.values() if not w.busy), None)
                    if worker is not None:
                        worker.busy = True
                        break
                    remaining = queue_deadline - time.monotonic()
                    if remaining <= 0:
                        self._counts["queue_timed_out"] += 1
                        return self._failure(
                            request.agent_name, TaskOutcome.TIMEOUT, "queue_timeout", began, request
                        )
                    self._condition.wait(timeout=min(0.05, remaining))
            # An idle crash is replaceable before dispatch; do not retry work already sent.
            if worker.broken.is_set() or not worker.process.is_alive():
                worker = self._replace(worker)
            if self._cancelled(stop):
                return self._failure(
                    request.agent_name, TaskOutcome.CANCELLED, "cancelled", began, request
                )
            worker.outgoing.put_nowait(frame)
            deadline = time.monotonic() + min(self.timeout_sec, request.deadline_sec)
            while True:
                if self._cancelled(stop):
                    recycle = True
                    with self._condition:
                        self._counts["cancelled"] += 1
                    return self._failure(
                        request.agent_name, TaskOutcome.CANCELLED, "cancelled", began, request
                    )
                if worker.broken.is_set() or not worker.process.is_alive():
                    recycle = True
                    with self._condition:
                        self._counts["crashed"] += 1
                    outcome = (
                        TaskOutcome.PROTOCOL_ERROR
                        if worker.fault == "protocol_error"
                        else TaskOutcome.WORKER_CRASH
                    )
                    return self._failure(request.agent_name, outcome, worker.fault, began, request)
                now = time.monotonic()
                if now >= deadline or now - worker.heartbeat_at > self.heartbeat_timeout_sec:
                    recycle = True
                    with self._condition:
                        self._counts["timed_out"] += 1
                    error = "execution_timeout" if now >= deadline else "heartbeat_timeout"
                    return self._failure(
                        request.agent_name, TaskOutcome.TIMEOUT, error, began, request
                    )
                try:
                    message = worker.incoming.get(timeout=min(0.05, deadline - now))
                except queue.Empty:
                    continue
                try:
                    if message.pop("type", None) != "response":
                        raise ValueError("Unexpected worker message")
                    response = WorkerResponse.model_validate(message)
                    for key in (
                        "task_id",
                        "agent_name",
                        "trace_id",
                        "correlation_id",
                        "causation_id",
                        "loop_id",
                    ):
                        if getattr(response, key) != getattr(request, key):
                            raise ValueError("Response identity mismatch")
                    if response.worker_id != worker.worker_id or (
                        response.ok and response.outcome != TaskOutcome.SUCCESS
                    ):
                        raise ValueError("Invalid response outcome or worker identity")
                except (ValueError, TypeError):
                    recycle = True
                    return self._failure(
                        request.agent_name,
                        TaskOutcome.PROTOCOL_ERROR,
                        "invalid_response",
                        began,
                        request,
                    )
                worker.tasks += 1
                recycle = worker.tasks >= self.max_tasks_per_worker or response.outcome in {
                    TaskOutcome.PROTOCOL_ERROR,
                    TaskOutcome.RESOURCE_EXHAUSTED,
                }
                return response.to_agent_result()
        except Exception:
            recycle = worker is not None
            return self._failure(
                request.agent_name, TaskOutcome.WORKER_CRASH, "worker_unavailable", began, request
            )
        finally:
            if worker is not None:
                if recycle:
                    try:
                        worker = self._replace(worker)
                    except Exception:
                        # Leave a stopped slot for a later request to replenish.
                        pass
                with self._condition:
                    worker.busy = False
                    self._condition.notify_all()
            self._admission.release()
            with self._condition:
                self._counts["completed"] += 1

    def _replace(self, worker: _Worker) -> _Worker:
        with self._lifecycle:
            self._retire(worker)
            if self._closing:
                return worker
            replacement = self._spawn()
            replacement.busy = True
            with self._condition:
                self._workers.pop(worker.worker_id, None)
                self._workers[replacement.worker_id] = replacement
                self._counts["restarted"] += 1
            return replacement

    def _cancelled(self, stop: threading.Event | None) -> bool:
        return self._abort.is_set() or (stop is not None and stop.is_set())

    @staticmethod
    def _failure(
        name: str,
        outcome: TaskOutcome,
        error: str,
        began: float,
        request: WorkerRequest | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            agent=name,
            content=f"[{name}] {error}",
            summary=error,
            meta={
                "error": error,
                "status": outcome.value,
                "outcome": outcome.value,
                "elapsed_sec": time.monotonic() - began,
                **(
                    {
                        "trace_id": request.trace_id,
                        "correlation_id": request.correlation_id,
                        "causation_id": request.causation_id,
                        "loop_id": request.loop_id,
                        "task_id": request.task_id,
                    }
                    if request
                    else {}
                ),
            },
        )

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        """Bounded Core helper threads (e.g. entity extraction), NOT isolated agents."""
        if self._closing or not self._aux_admission.acquire(blocking=False):
            raise RuntimeError("Core helper queue is closed or full")
        try:
            future = self._aux.submit(fn, *args, **kwargs)
        except Exception:
            self._aux_admission.release()
            raise
        with self._condition:
            self._aux_futures.add(future)
        future.add_done_callback(self._aux_completed)
        return future

    def _aux_completed(self, future: Future[Any]) -> None:
        with self._condition:
            self._aux_futures.discard(future)
            self._condition.notify_all()
        self._aux_admission.release()

    @property
    def is_idle(self) -> bool:
        with self._condition:
            return (
                not self._aux_futures
                and self._counts["submitted"] == self._counts["completed"]
                and not any(worker.busy for worker in self._workers.values())
            )

    def shutdown(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            deadline = time.monotonic() + self.shutdown_grace_sec
            while any(w.busy for w in self._workers.values()) and time.monotonic() < deadline:
                self._condition.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
        self._abort.set()
        with self._lifecycle:
            for worker in self.active_workers.values():
                self._retire(worker, graceful=not worker.busy)
            self._started = False
        self._aux.shutdown(wait=False, cancel_futures=True)

    @property
    def stats(self) -> dict[str, Any]:
        with self._condition:
            return {
                "backend": "process",
                "max_workers": self.max_workers,
                "timeout_sec": self.timeout_sec,
                "queue_capacity": self.queue_capacity,
                "queue_timeout_sec": self.queue_timeout_sec,
                "max_tasks_per_worker": self.max_tasks_per_worker,
                "supported_agents": ["chat_agent", "docs_agent"],
                "tools_available": False,
                "os_sandbox": False,
                "closing": self._closing,
                "pending": max(0, self._counts["submitted"] - self._counts["completed"]) + len(self._aux_futures),
                "aux_pending": len(self._aux_futures),
                **self._counts,
                "workers": [
                    {
                        "worker_id": w.worker_id,
                        "pid": w.process.pid,
                        "alive": w.process.is_alive(),
                        "busy": w.busy,
                        "tasks": w.tasks,
                        "heartbeat_age_sec": round(time.monotonic() - w.heartbeat_at, 2),
                    }
                    for w in self._workers.values()
                ],
            }
