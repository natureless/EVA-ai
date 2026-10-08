from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import threading
from typing import Any, Callable
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler

from event.event_bus import EventBus
from event.event_schema import Event
from event.codec import source_event_identity


class RuntimeScheduler:
    def __init__(
        self,
        event_bus: EventBus,
        snapshot_save_fn: Callable[[], None],
        system_state: dict[str, Any],
        tick_interval_sec: int = 10,
        maintenance_interval_sec: int = 60,
        snapshot_interval_sec: int = 120,
    ) -> None:
        self.event_bus = event_bus
        self.snapshot_save_fn = snapshot_save_fn
        self.system_state = system_state
        self.tick_interval_sec = tick_interval_sec
        self.maintenance_interval_sec = maintenance_interval_sec
        self.snapshot_interval_sec = snapshot_interval_sec

        self._scheduler = BackgroundScheduler()
        self._started = False
        self._closing = False
        self._activity = threading.Condition()
        self._active_jobs = 0
        self._event_source_run = uuid4().hex

    @property
    def is_running(self) -> bool:
        return self._started and not self._closing and self._scheduler.running

    def start(self) -> None:
        if self._closing:
            raise RuntimeError("scheduler cannot restart after shutdown")
        if self._started:
            return
        logger = logging.getLogger("eva.scheduler")
        logger.info("scheduler starting")

        self._scheduler.add_job(
            self._run_tracked,
            args=(self._publish_system_tick,),
            trigger="interval",
            seconds=self.tick_interval_sec,
            id="system_tick_job",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self._scheduler.add_job(
            self._run_tracked,
            args=(self._publish_maintenance,),
            trigger="interval",
            seconds=self.maintenance_interval_sec,
            id="maintenance_job",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self._scheduler.add_job(
            self._run_tracked,
            args=(self._save_snapshot,),
            trigger="interval",
            seconds=self.snapshot_interval_sec,
            id="snapshot_job",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self._scheduler.start()
        self._started = True
        self.system_state["scheduler_running"] = True
        logger.info("scheduler started")

    def shutdown(self, timeout: float = 3.0) -> bool:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("shutdown timeout must be finite and nonnegative")
        logger = logging.getLogger("eva.scheduler")
        with self._activity:
            self._closing = True
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
        with self._activity:
            stopped = self._activity.wait_for(lambda: self._active_jobs == 0, timeout=timeout)
            self.system_state["scheduler_running"] = False
            self.system_state["scheduler_active_jobs"] = self._active_jobs
        logger.info("scheduler stop completed=%s", stopped)
        return stopped

    def _run_tracked(self, action: Callable[[], None]) -> None:
        with self._activity:
            if self._closing:
                return
            self._active_jobs += 1
        try:
            action()
        finally:
            with self._activity:
                self._active_jobs -= 1
                self._activity.notify_all()

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for job in self._scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
        return jobs

    def _publish_system_tick(self, observed_at: datetime | None = None) -> None:
        logger = logging.getLogger("eva.scheduler")
        stamp = observed_at or datetime.now(timezone.utc)
        origin = f"{self._event_source_run}:system_tick:{stamp.isoformat()}"
        event = Event(
            id=source_event_identity("scheduler", "system_tick", origin), source_event_id=origin, timestamp=stamp,
            type="system_tick",
            source="scheduler",
            payload={"scheduled_at": stamp.isoformat()},
        )
        self.event_bus.publish(event)
        self.system_state["pending_events"] = self.event_bus.size()
        logger.debug("system_tick published")

    def _publish_maintenance(self, observed_at: datetime | None = None) -> None:
        logger = logging.getLogger("eva.scheduler")
        stamp = observed_at or datetime.now(timezone.utc)
        origin = f"{self._event_source_run}:maintenance:{stamp.isoformat()}"
        event = Event(
            id=source_event_identity("scheduler", "maintenance", origin), source_event_id=origin, timestamp=stamp,
            type="maintenance",
            source="scheduler",
            payload={
                "scheduled_at": stamp.isoformat(),
                "kind": "periodic_maintenance",
            },
        )
        self.event_bus.publish(event)
        self.system_state["pending_events"] = self.event_bus.size()
        logger.debug("maintenance published")

    def _save_snapshot(self) -> None:
        logger = logging.getLogger("eva.scheduler")
        self.snapshot_save_fn()
        logger.info("periodic snapshot saved")
