"""Managed publisher for proven, pre-startup unclaimed HTTP requests.

No handler/model/tool execution here. A handoff can repeat after a crash;
the stable processor's durable claim is the irreversible execution boundary.
New live HTTP reservations are never swept into this publisher's candidate set.
"""

import logging
import math
import threading
import time
from typing import Any, Callable

from event.event_bus import EventBus
from runtime.result_registry import ResultRegistry


class RequestRecoveryPublisher:
    def __init__(
        self,
        store: Any,
        registry: ResultRegistry,
        event_bus: EventBus,
        *,
        accepting: Callable[[], bool],
        poll_sec: float = 0.1,
    ):
        if isinstance(poll_sec, bool) or not math.isfinite(poll_sec) or poll_sec <= 0:
            raise ValueError("invalid request recovery interval")
        self.store = store
        self.registry = registry
        self.event_bus = event_bus
        self.accepting = accepting
        self.poll_sec = poll_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._cursor = ""
        self._submitted: set[str] = set()
        self._published = 0
        self._backpressure = 0
        self._errors = 0
        self._closed = False

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("request recovery publisher is stopped")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="eva-request-recovery", daemon=True
            )
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                with self._lock:
                    self._errors += 1
                # Avoid dumping persisted user text or repeating a traceback each tick.
                if self._errors == 1:
                    logging.getLogger("eva.request_recovery").error(
                        "request recovery unavailable; no handler was invoked by the publisher"
                    )
            self._stop.wait(self.poll_sec)

    def step(self):
        with self._operation:
            if self._stop.is_set() or self.accepting() is not True:
                return 0
            # Ack failures after a successful queue handoff retry only the ack.
            for task_id in tuple(self._submitted):
                self.store.acknowledge_resume(task_id)
                self._submitted.remove(task_id)
            page = self.store.resume_page(after_task_id=self._cursor, limit=16)
            sent = 0
            for event in page["events"]:
                if self._stop.is_set() or self.accepting() is not True:
                    return sent
                restored = self.registry.restore_pending(event)
                if restored == "capacity_full":
                    self._backpressure += 1
                    continue
                if restored == "event_mismatch":
                    raise ValueError("request recovery identity conflict")
                if restored == "pending":
                    if not self.event_bus.publish(event):
                        self._backpressure += 1
                        continue
                    self._submitted.add(event.correlation_id)
                    self._published += 1
                    sent += 1
                self.store.acknowledge_resume(event.correlation_id)
                self._submitted.discard(event.correlation_id)
            self._cursor = page["next_cursor"] or ""
            # Deliver pending compact notifications independently of queue admission.
            sink = self.store.receipt_sink
            if sink is not None:
                self.store.deliver_receipts(sink)
            return sent

    def stop(self, timeout=3.0):
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("invalid request recovery shutdown timeout")
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        if thread is not None and thread.is_alive():
            return False
        if not self._operation.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        self._operation.release()
        return True

    @property
    def stats(self):
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "published": self._published,
                "backpressure": self._backpressure,
                "errors": self._errors,
                "closed": self._closed,
            }
