"""IntentParser — MVSC 意图解析器。

将 EventEnvelope 解析为结构化 Intent。
从现有 Planner 中分离意图解析职责。

解析策略:
1. user_message → chat intent
2. maintenance → maintenance intent
3. reminder_trigger → reminder intent
4. 其他 → system intent
"""

from __future__ import annotations

import logging
from typing import Any

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.protocols import Intent
from packages.contracts.state import ConsciousState

logger = logging.getLogger("eva.agentos.intent_parser")


class IntentParser:
    """将事件解析为结构化意图。

    Usage::

        parser = IntentParser()
        intent = await parser.parse(event, state)
    """

    def __init__(self) -> None:
        self._parse_count = 0

    async def parse(
        self,
        event: EventEnvelope,
        state: ConsciousState | None = None,
    ) -> Intent:
        """解析事件为意图。"""
        self._parse_count += 1

        intent_id = f"int_{event.event_id[:12]}"

        # ── User message → chat intent ──
        if event.event_type == EventFamily.PERCEPTION.USER_MESSAGE:
            text = event.payload.get("text", "")
            return Intent(
                intent_id=intent_id,
                description=text[:200],
                priority=0.8,
                constraints=self._extract_constraints(text),
            )

        # ── Maintenance → maintenance intent ──
        if event.event_type == EventFamily.LIFECYCLE.MAINTENANCE_STARTED:
            return Intent(
                intent_id=intent_id,
                description="Perform system maintenance",
                priority=0.5,
            )

        # ── System events ──
        if event.event_type.startswith("perception."):
            return Intent(
                intent_id=intent_id,
                description=f"Process: {event.event_type}",
                priority=0.3,
            )

        # ── Default ──
        return Intent(
            intent_id=intent_id,
            description=f"Handle: {event.event_type}",
            priority=0.2,
        )

    def _extract_constraints(self, text: str) -> list[str]:
        """从文本中提取约束条件。"""
        constraints: list[str] = []
        text_lower = text.lower()

        if "urgent" in text_lower or "asap" in text_lower:
            constraints.append("time_sensitive")
        if "careful" in text_lower or "safe" in text_lower:
            constraints.append("safety_critical")
        if "accurate" in text_lower or "precise" in text_lower:
            constraints.append("high_accuracy")

        return constraints

    @property
    def stats(self) -> dict[str, Any]:
        return {"parse_count": self._parse_count}
