"""EventBusAdapter — 将现有同步 EventBus 适配为 MVSC EventBusProtocol。

包装现有 event/event_bus.py 的 EventBus，提供:
1. async publish(EventEnvelope) — 自动转换 LegacyEvent ↔ EventEnvelope
2. async consume(timeout) — 返回 EventEnvelope
3. subscribe(event_type, handler) — 按事件类型订阅

同时将事件持久化到 EventStore，实现 append-only 事件溯源。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from event.event_schema import Event as LegacyEvent
from packages.contracts.events import EventEnvelope, EventFamily, from_legacy_event
from packages.kernel.event_store import EventStore

logger = logging.getLogger("eva.kernel.event_bus_adapter")


# ═══════════════════════════════════════════════════════════════
# 反向转换: EventEnvelope → LegacyEvent
# ═══════════════════════════════════════════════════════════════

# 新事件类型 → 旧事件类型映射 (best-effort)
_ENVELOPE_TO_LEGACY: dict[str, str] = {
    EventFamily.PERCEPTION.USER_MESSAGE: "user_message",
    EventFamily.LIFECYCLE.MAINTENANCE_STARTED: "maintenance",
    EventFamily.PERCEPTION.SYSTEM_EVENT: "reminder_trigger",
    EventFamily.PERCEPTION.SCHEDULER_TICK: "system_tick",
}


def to_legacy_event(envelope: EventEnvelope) -> LegacyEvent:
    """将 EventEnvelope 转换回 LegacyEvent（向下兼容）。"""
    legacy_type = _ENVELOPE_TO_LEGACY.get(envelope.event_type, "system_tick")
    return LegacyEvent(
        id=envelope.event_id,
        type=legacy_type,
        source=envelope.source,
        timestamp=envelope.timestamp,
        payload=envelope.payload,
        correlation_id=envelope.correlation_id,
    )


# ═══════════════════════════════════════════════════════════════
# EventBusAdapter
# ═══════════════════════════════════════════════════════════════

class EventBusAdapter:
    """将现有 EventBus 适配为 MVSC 事件总线。

    特性:
    - 双模式: 可以同时发布到旧 EventBus 和新 EventStore
    - 自动类型转换: LegacyEvent ↔ EventEnvelope
    - 线程安全: 使用 asyncio 桥接同步 EventBus
    - 订阅管理: 按 event_type 前缀匹配分发

    Usage::

        legacy_bus = EventBus(s5_store=...)
        event_store = EventStore("data/event_store.db")
        adapter = EventBusAdapter(legacy_bus, event_store)

        # 发布新格式事件
        evt = EventEnvelope(event_type="perception.user_message_received", ...)
        await adapter.publish(evt)

        # 消费事件（自动转换为新格式）
        envelope = await adapter.consume(timeout=0.5)
    """

    def __init__(
        self,
        legacy_bus: Any,  # event.event_bus.EventBus
        event_store: EventStore | None = None,
        subject_id: str = "eva-001",
    ) -> None:
        self._bus = legacy_bus
        self._store = event_store
        self._subject_id = subject_id

        # 订阅管理: {event_type_prefix: [handler, ...]}
        self._subscriptions: dict[str, list[Any]] = {}
        self._sub_lock = threading.Lock()

        # 统计
        self._published_count = 0
        self._consumed_count = 0
        self._persisted_count = 0

    # ── publish ──────────────────────────────────────────────

    async def publish(self, event: EventEnvelope) -> bool:
        """发布事件。

        1. 将 EventEnvelope 持久化到 EventStore (如果配置)
        2. 转换为 LegacyEvent 发布到现有 EventBus
        3. 通知匹配的订阅者

        Returns:
            True 如果事件成功入队
        """
        event.subject_id = self._subject_id

        # 1. 持久化到 EventStore
        if self._store is not None:
            try:
                self._store.append(event)
                self._persisted_count += 1
            except Exception:
                logger.exception("failed to persist event to EventStore")

        # 2. 转换并发布到旧 EventBus
        legacy = to_legacy_event(event)
        enqueued = self._bus.publish(legacy)

        # 3. 通知订阅者
        if enqueued:
            self._published_count += 1
            await self._notify_subscribers(event)

        return enqueued

    async def publish_legacy(self, legacy_event: LegacyEvent) -> bool:
        """发布旧格式事件（自动升级为 EventEnvelope）。

        用于现有代码继续使用 LegacyEvent 的场景。
        """
        envelope = from_legacy_event(legacy_event, self._subject_id)
        return await self.publish(envelope)

    # ── consume ──────────────────────────────────────────────

    async def consume(self, timeout: float = 0.5) -> EventEnvelope | None:
        """消费事件，返回 EventEnvelope。

        内部调用同步 EventBus.consume() 并自动转换。
        """
        loop = asyncio.get_event_loop()
        try:
            legacy = await loop.run_in_executor(
                None, self._bus.consume, timeout,
            )
        except Exception:
            return None

        if legacy is None:
            return None

        self._consumed_count += 1
        return from_legacy_event(legacy, self._subject_id)

    # ── subscribe ────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Any) -> None:
        """订阅特定事件类型（支持前缀匹配）。

        Args:
            event_type: 事件类型或前缀，如 "perception." 匹配所有感知事件
            handler: async callable(event: EventEnvelope) -> None
        """
        with self._sub_lock:
            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []
            self._subscriptions[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Any) -> None:
        """取消订阅。"""
        with self._sub_lock:
            if event_type in self._subscriptions:
                try:
                    self._subscriptions[event_type].remove(handler)
                except ValueError:
                    pass

    async def _notify_subscribers(self, event: EventEnvelope) -> None:
        """通知所有匹配的订阅者。"""
        with self._sub_lock:
            handlers: list[Any] = []
            for prefix, subs in self._subscriptions.items():
                if event.event_type.startswith(prefix):
                    handlers.extend(subs)

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception:
                logger.exception("subscriber error for %s", event.event_type)

    # ── stats ────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        legacy_stats = self._bus.stats() if hasattr(self._bus, "stats") else {}
        return {
            "adapter_published": self._published_count,
            "adapter_consumed": self._consumed_count,
            "adapter_persisted": self._persisted_count,
            "subscriptions": len(self._subscriptions),
            "legacy_bus": legacy_stats,
        }
