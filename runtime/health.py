from __future__ import annotations

from typing import Any


class HealthService:
    """System health probe service.

    Accepts live component references at bootstrap so readiness checks
    are real: database ping, thread liveness, event bus depth, and LLM
    provider. Falls back to boolean flags when a component reference is
    not available.
    """

    def __init__(
        self,
        system_state: dict[str, Any],
        *,
        store: Any = None,
        event_bus: Any = None,
        loop: Any = None,
        scheduler: Any = None,
    ) -> None:
        self.system_state = system_state
        self._store = store
        self._event_bus = event_bus
        self._loop = loop
        self._scheduler = scheduler

    def live(self) -> dict[str, Any]:
        return {"status": "alive"}

    def ready(self) -> dict[str, Any]:
        result = {
            "status": "ready" if self.system_state.get("ready", False) else "not_ready",
            "components": {
                "db": self._probe_db(),
                "event_bus": self._probe_event_bus(),
                "event_persistence": self._probe_event_persistence(),
                "planner": self.system_state.get("planner_ready", False),
                "registry": self.system_state.get("registry_ready", False),
                "result_registry": self.system_state.get("result_registry_ready", False),
                "snapshot": self.system_state.get("snapshot_ready", False),
                "profile": self.system_state.get("profile_ready", False),
                "persona": self.system_state.get("persona_ready", False),
                "self_model": self.system_state.get("self_model_ready", False),
                "scheduler": self._probe_scheduler(),
                "proactive": self.system_state.get("proactive_ready", False),
                "cognition_loop": self._probe_cognition_loop(),
                "llm": self._probe_llm(),
            },
            "runtime": {
                "scheduler_running": self.system_state.get("scheduler_running", False),
                "last_snapshot_at": self.system_state.get("last_snapshot_at"),
                "last_proactive_reason": self.system_state.get("last_proactive_reason"),
                "pending_events": self._probe_event_depth(),
                "bootstrap_sec": self.system_state.get("bootstrap_sec"),
            },
        }

        # ── MVSC status (when enabled) ──
        mvsc_enabled = bool(self.system_state.get("mvsc_feature_flags"))
        if mvsc_enabled:
            result["mvsc"] = {
                "enabled": True,
                "tick": self.system_state.get("mvsc_tick", 0),
                "mode": self.system_state.get("mvsc_runtime_mode", "unknown"),
                "phase": self.system_state.get("mvsc_cognition_phase", "unknown"),
            }

        return result

    def _probe_db(self) -> bool:
        if self._store is not None:
            try:
                result = self._store.fetchall("SELECT 1", ())
                return result is not None
            except Exception:
                return False
        return self.system_state.get("db_ready", False)  # type: ignore[no-any-return]

    def _probe_event_bus(self) -> bool:
        if self._event_bus is not None:
            try:
                depth = self._event_bus.size()
                healthy = getattr(self._event_bus, "_persist_healthy", True)
                return depth >= 0 and healthy
            except Exception:
                return False
        return self.system_state.get("event_bus_ready", False)  # type: ignore[no-any-return]

    def _probe_event_persistence(self) -> bool:
        if self._event_bus is not None:
            try:
                return getattr(self._event_bus, "_persist_healthy", True)
            except Exception:
                return False
        return True

    def _probe_event_depth(self) -> int:
        if self._event_bus is not None:
            try:
                return self._event_bus.size()  # type: ignore[no-any-return]
            except Exception:
                return -1
        return self.system_state.get("pending_events", 0)  # type: ignore[no-any-return]

    def _probe_cognition_loop(self) -> bool:
        if self._loop is not None:
            try:
                t = getattr(self._loop, "_thread", None)
                return t is not None and t.is_alive()
            except Exception:
                return False
        return self.system_state.get("loop_ready", False)  # type: ignore[no-any-return]

    def _probe_scheduler(self) -> bool:
        if self._scheduler is not None:
            try:
                sched = getattr(self._scheduler, "_scheduler", None)
                if sched is not None:
                    return getattr(sched, "running", False)
                return getattr(self._scheduler, "_started", False)
            except Exception:
                return False
        return self.system_state.get("scheduler_ready", False)  # type: ignore[no-any-return]

    def _probe_llm(self) -> bool:
        try:
            from core.llm_adapter import get_llm, MockLLM

            llm = get_llm()
            return not isinstance(llm, MockLLM)
        except Exception:
            return False
