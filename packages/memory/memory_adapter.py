"""TieredMemoryAdapter — 将现有 TieredMemoryManager 适配为 MVSC MemoryProtocol。

包装 memory/tiered_store.py 的 TieredMemoryManager，提供:
1. consolidate() — 记忆整合（MVSC 风格）
2. retrieve() — 多因素检索评分

与现有代码的关系:
- TieredMemoryManager 保持不变的继续使用
- 此适配器仅在使用 MVSC Pipeline 时作为 MemoryProtocol 的实现
"""

from __future__ import annotations

import logging
from typing import Any

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import BroadcastContent, ConsciousState

logger = logging.getLogger("eva.memory.adapter")


class TieredMemoryAdapter:
    """将 TieredMemoryManager 适配为 MVSC MemoryProtocol。

    Usage::

        tm = container.tiered_memory
        adapter = TieredMemoryAdapter(tm)

        # In AdaptedCognitionLoop:
        events = await adapter.consolidate(state, event, broadcast, eval, actions)
        results = await adapter.retrieve("user query", context={})
    """

    def __init__(self, tiered_memory: Any, cache: Any = None) -> None:
        """Args:
            tiered_memory: TieredMemoryManager 实例
            cache: SemanticCache 实例 (可选)
        """
        self._tm = tiered_memory
        self._cache = cache

    # ── consolidate ──────────────────────────────────────────

    async def consolidate(
        self,
        state: ConsciousState,
        source_event: EventEnvelope,
        broadcast: list[BroadcastContent],
        evaluation: dict,
        action_events: list[EventEnvelope],
    ) -> list[EventEnvelope]:
        """将当前认知循环的内容整合到分层记忆中。

        对每个广播内容:
        - 根据重要性路由到 S1/S2/S3
        - 产生 memory.episode_committed 事件

        对行动事件:
        - 提取工具调用结果写入记忆
        """
        events: list[EventEnvelope] = []

        if self._tm is None:
            return events

        try:
            # ── 广播内容 → 记忆 ──
            for bc in broadcast:
                importance = bc.content.priority

                self._tm.ingest(
                    bc.content.summary,
                    importance=importance,
                    source=bc.content.source_module,
                    category=bc.content.content_type,
                    source_event_id=source_event.event_id,
                    self_model_delta=evaluation.get("self_model_delta", 0.0),
                    prediction_error=evaluation.get("prediction_error", 0.0),
                )

                events.append(EventEnvelope(
                    event_type=EventFamily.MEMORY.EPISODE_COMMITTED,
                    source="memory_adapter",
                    causation_id=source_event.event_id,
                    payload={
                        "content_id": bc.content.content_id,
                        "content_type": bc.content.content_type,
                        "importance": importance,
                        "tiers": self._tiers_for_importance(importance),
                    },
                ))

            # ── 行动事件 → 记忆 ──
            for ae in action_events:
                if ae.event_type in (
                    EventFamily.ACTION.TOOL_COMPLETED,
                    EventFamily.ACTION.AGENT_COMPLETED,
                ):
                    self._tm.ingest(
                        str(ae.payload.get("summary", ae.payload.get("ok", ""))),
                        importance=0.6,  # 行动结果默认中等重要性
                        source=ae.source,
                        category="action_result",
                        source_event_id=ae.event_id,
                    )

            # ── 定期维护 ──
            if state.tick % 100 == 0:
                maint = self._tm.maintenance()
                logger.debug("memory maintenance: %s", maint)

        except Exception:
            logger.exception("memory consolidation failed")

        return events

    # ── retrieve ─────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        context: dict | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """多因素记忆检索（带语义缓存）。

        检索评分考虑:
        - Similarity (语义+FTS5)
        - Confidence (来源可靠性)
        - Freshness (时间衰减)
        - GoalRelevance (目标相关性)

        缓存: 如果配置了 SemanticCache，优先从缓存返回。
        """
        ctx = context or {}

        if self._tm is None:
            return []

        # ── 缓存查找 ──
        if self._cache is not None:
            cached = self._cache.get(query)
            if cached is not None:
                return cached[:limit]

        try:
            # 使用现有 recall (已包含 S1+S2+S3)
            raw_results = self._tm.recall(query, tiers=[1, 2, 3])

            # ── 多因素评分 ──
            scored: list[dict[str, Any]] = []
            for r in raw_results:
                score = self._compute_retrieval_score(r, query, ctx)
                r["_retrieval_score"] = round(score, 4)
                scored.append(r)

            # 按评分排序
            scored.sort(key=lambda r: r.get("_retrieval_score", 0), reverse=True)

            # ── 写入缓存 ──
            if self._cache is not None:
                self._cache.put(query, scored)

            return scored[:limit]

        except Exception:
            logger.exception("memory retrieval failed")
            return []

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _tiers_for_importance(importance: float) -> list[str]:
        """根据重要性返回被写入的层级。"""
        tiers = ["S1"]
        if importance >= 0.6:
            tiers.append("S2")
        if importance >= 0.8:
            tiers.append("S3")
        return tiers

    @staticmethod
    def _compute_retrieval_score(
        result: dict[str, Any],
        query: str,
        context: dict,
    ) -> float:
        """计算多因素检索评分。

        RetrievalScore = Similarity × Confidence × Authority
                        × Freshness × GoalRelevance
        """
        score = 1.0

        # Similarity: 基于内容匹配度（简化版）
        content = str(result.get("content", "")).lower()
        query_lower = query.lower()
        if query_lower and content:
            # 简单的词重叠率
            query_words = set(query_lower.split())
            content_words = set(content.split())
            if query_words:
                overlap = len(query_words & content_words) / len(query_words)
                score *= 0.5 + 0.5 * overlap  # [0.5, 1.0]

        # Confidence: 从结果中提取或使用默认值
        confidence = float(result.get("confidence", 0.5))
        score *= 0.5 + 0.5 * confidence  # [0.5, 1.0]

        # Authority: 来源可靠性
        source = str(result.get("source", ""))
        authority = 0.9 if source == "user" else 0.7 if source else 0.5
        score *= authority

        # Freshness: 时间衰减
        created = result.get("created_at", "")
        if created:
            try:
                from datetime import datetime, timezone
                age_hours = 0.0
                if isinstance(created, str):
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                # 指数衰减: 7天半衰期
                import math
                freshness = math.exp(-age_hours / (7 * 24))
                score *= 0.3 + 0.7 * freshness  # [0.3, 1.0]
            except Exception:
                pass

        # GoalRelevance: 与当前目标的关联
        goals = context.get("goals", [])
        if goals:
            goal_text = " ".join(str(g) for g in goals).lower()
            if any(word in goal_text for word in query_lower.split()):
                score *= 1.2  # 目标相关加分

        return min(1.0, max(0.0, score))
