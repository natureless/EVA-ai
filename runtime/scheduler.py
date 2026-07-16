from datetime import datetime, timezone
import logging
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

from event.event_bus import EventBus
from event.event_schema import Event


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

    def start(self) -> None:
        if self._started:
            return
        logger = logging.getLogger("eva.scheduler")
        logger.info("scheduler starting")

        self._scheduler.add_job(
            self._publish_system_tick,
            trigger="interval",
            seconds=self.tick_interval_sec,
            id="system_tick_job",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self._scheduler.add_job(
            self._publish_maintenance,
            trigger="interval",
            seconds=self.maintenance_interval_sec,
            id="maintenance_job",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self._scheduler.add_job(
            self._save_snapshot,
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

    def shutdown(self) -> None:
        if not self._started:
            return
        logger = logging.getLogger("eva.scheduler")

        self._scheduler.shutdown(wait=False)
        self._started = False
        self.system_state["scheduler_running"] = False
        logger.info("scheduler stopped")

    def list_jobs(self) -> list[dict]:
        jobs: list[dict] = []
        for job in self._scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
        return jobs

    def _publish_system_tick(self) -> None:
        logger = logging.getLogger("eva.scheduler")
        event = Event(
            type="system_tick",
            source="scheduler",
            payload={"scheduled_at": datetime.now(timezone.utc).isoformat()},
        )
        self.event_bus.publish(event)
        self.system_state["pending_events"] = self.event_bus.size()
        logger.debug("system_tick published")

    def _publish_maintenance(self) -> None:
        logger = logging.getLogger("eva.scheduler")
        event = Event(
            type="maintenance",
            source="scheduler",
            payload={
                "scheduled_at": datetime.now(timezone.utc).isoformat(),
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
