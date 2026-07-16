class HealthService:
    def __init__(self, system_state: dict) -> None:
        self.system_state = system_state

    def live(self) -> dict:
        return {"status": "alive"}

    def ready(self) -> dict:
        return {
            "status": "ready" if self.system_state.get("ready", False) else "not_ready",
            "components": {
                "db": self.system_state.get("db_ready", False),
                "event_bus": self.system_state.get("event_bus_ready", False),
                "planner": self.system_state.get("planner_ready", False),
                "registry": self.system_state.get("registry_ready", False),
                "result_registry": self.system_state.get("result_registry_ready", False),
                "snapshot": self.system_state.get("snapshot_ready", False),
                "profile": self.system_state.get("profile_ready", False),
                "persona": self.system_state.get("persona_ready", False),
                "self_model": self.system_state.get("self_model_ready", False),
                "scheduler": self.system_state.get("scheduler_ready", False),
                "proactive": self.system_state.get("proactive_ready", False),
                "cognition_loop": self.system_state.get("loop_ready", False),
            },
            "runtime": {
                "scheduler_running": self.system_state.get("scheduler_running", False),
                "last_snapshot_at": self.system_state.get("last_snapshot_at"),
                "last_proactive_reason": self.system_state.get("last_proactive_reason"),
            },
        }
