from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProactiveDecision:
    should_trigger: bool
    reason: str
    payload: dict[str, Any]


class ProactiveEngine:
    def __init__(
        self,
        stagnation_threshold_sec: int = 86400,
        reminder_cooldown_sec: int = 43200,
    ) -> None:
        self.stagnation_threshold_sec = stagnation_threshold_sec
        self.reminder_cooldown_sec = reminder_cooldown_sec

    def evaluate_stagnation(
        self,
        *,
        world_model: dict,
        proactive_state: dict,
        now_ts: float,
    ) -> ProactiveDecision:
        focus = str(world_model.get("focus", "")).strip()
        if not focus or focus == "idle":
            return ProactiveDecision(
                should_trigger=False,
                reason="no_focus",
                payload={},
            )

        last_user_message_ts = proactive_state.get("last_user_message_ts")
        if not last_user_message_ts:
            return ProactiveDecision(
                should_trigger=False,
                reason="no_user_activity_baseline",
                payload={},
            )

        idle_sec = max(0, int(now_ts - last_user_message_ts))
        if idle_sec < self.stagnation_threshold_sec:
            return ProactiveDecision(
                should_trigger=False,
                reason="below_threshold",
                payload={"idle_sec": idle_sec},
            )

        last_reminder_ts = proactive_state.get("last_reminder_ts")
        if last_reminder_ts and (now_ts - last_reminder_ts) < self.reminder_cooldown_sec:
            return ProactiveDecision(
                should_trigger=False,
                reason="cooldown_active",
                payload={
                    "idle_sec": idle_sec,
                    "cooldown_remaining_sec": int(
                        self.reminder_cooldown_sec - (now_ts - last_reminder_ts)
                    ),
                },
            )

        return ProactiveDecision(
            should_trigger=True,
            reason="stagnation_detected",
            payload={
                "focus": focus,
                "idle_sec": idle_sec,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def build_reminder_message(self, *, focus: str, idle_sec: int) -> str:
        hours = round(idle_sec / 3600, 1)
        return f"[proactive] Focus '{focus}' idle for ~{hours} hours. Continue?"
