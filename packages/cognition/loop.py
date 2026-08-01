"""EVA-MVSC 统一认知循环 — 分阶段Pipeline实现。

这是MVSC框架与现有EVA代码融合后的核心认知循环。

从当前 CognitionLoop._process_one() 的单方法处理，
升级为13阶段的Pipeline:
    PERCEIVE → UPDATE_WORLD → UPDATE_BODY → GENERATE_CONTENT
    → SELECT_ATTENTION → BROADCAST → ATTRIBUTE_SELF
    → EVALUATE → DECIDE → PLAN → ACT → VERIFY → CONSOLIDATE

关键设计决策:
1. 每个阶段是独立的 CognitiveModule
2. 阶段间通过 EventEnvelope 通信
3. 可以通过 feature_flags 关闭任意阶段（消融实验）
4. 保留与现有代码的完全向后兼容

与现有代码的关系:
- 现有 CognitionLoop 作为向后兼容的简化路径保留
- 新的 PipelineCognitionLoop 可以通过 feature flag 启用
- 两个实现共享相同的 EventBus、Memory、WorldModel 等后端
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import (
    BroadcastContent,
    CognitionPhase,
    ConsciousState,
    ContentCandidate,
    RuntimeMode,
)

logger = logging.getLogger("eva.cognition.pipeline")


# ═══════════════════════════════════════════════════════════════
# ContentEngine — 候选内容生成
# ═══════════════════════════════════════════════════════════════

class ContentEngine:
    """从世界/身体/事件变化生成候选意识内容。

    候选内容包括:
    - 对用户消息的响应建议
    - 风险警报
    - 计划建议
    - 反思内容
    """

    def __init__(self, llm_adapter: Any = None) -> None:
        self._llm = llm_adapter

    async def generate(
        self,
        state: ConsciousState,
        event: EventEnvelope,
        world_delta: dict | None = None,
        body_delta: dict | None = None,
    ) -> list[ContentCandidate]:
        """生成候选内容列表。"""
        candidates: list[ContentCandidate] = []

        # 1. 用户消息 → 响应候选
        if event.event_type == EventFamily.PERCEPTION.USER_MESSAGE:
            text = event.payload.get("text", "")
            candidates.append(ContentCandidate(
                content_type="response",
                summary=f"Respond to user: {text[:80]}",
                source_module="content_engine",
                salience=0.8,
                goal_relevance=0.7,
                novelty=0.5,
            ))

        # 2. 身体风险 → 警报候选
        if body_delta:
            try:
                merged = state.body.model_copy(update=body_delta)
                from packages.contracts.state import ViabilityBounds
                bounds = ViabilityBounds()
                issues = bounds.check(merged)
                if issues:
                    for issue in issues:
                        candidates.append(ContentCandidate(
                            content_type="risk_alert",
                            summary=f"Viability risk: {issue}",
                            source_module="content_engine",
                            viability_risk=0.9,
                            salience=0.7,
                            novelty=0.6,
                        ))
            except Exception:
                pass  # body delta merge failure is non-critical

        # 3. 世界模型变化 → 反思候选
        if world_delta and world_delta.get("focus_changed"):
            candidates.append(ContentCandidate(
                content_type="reflection",
                summary=f"World focus changed: {world_delta.get('focus', '')}",
                source_module="content_engine",
                novelty=0.7,
                goal_relevance=0.5,
            ))

        # 4. 如果无事可做，生成默认内容
        if not candidates:
            candidates.append(ContentCandidate(
                content_type="idle",
                summary="System idle — no action required",
                source_module="content_engine",
                salience=0.1,
            ))

        return candidates


# ═══════════════════════════════════════════════════════════════
# Attention — 可解释的注意力竞争
# ═══════════════════════════════════════════════════════════════

class Attention:
    """可解释的注意力选择机制。

    优先级公式:
        Priority(c) = w_s*Salience + w_g*GoalRelevance + w_v*ViabilityRisk
                     + w_n*Novelty + w_u*Uncertainty + w_d*Deadline
                     + w_e*ExpectedImpact - w_i*Inhibition

    每个候选保留各分量得分，确保事后可解释为什么它获得注意。
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.weights = weights or {
            "salience": 0.20,
            "goal_relevance": 0.20,
            "viability_risk": 0.25,
            "novelty": 0.10,
            "uncertainty": 0.10,
            "deadline": 0.05,
            "expected_impact": 0.10,
            "inhibition": 0.10,
        }

    async def select(
        self,
        state: ConsciousState,
        candidates: list[ContentCandidate],
    ) -> list[ContentCandidate]:
        """选择获得注意的内容（按优先级排序）。

        每个候选的 priority 属性已计算好各分量得分。
        我们只需要排序和阈值过滤。
        """
        # 按优先级排序
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.priority,
            reverse=True,
        )

        # 过滤: 优先级 > 0.1 的才进入下一步
        selected = [c for c in sorted_candidates if c.priority > 0.1]

        # 限制最多5个内容进入工作空间
        return selected[:5]


# ═══════════════════════════════════════════════════════════════
# Workspace — 全局工作空间
# ═══════════════════════════════════════════════════════════════

class Workspace:
    """全局工作空间 — 广播门控。

    广播条件:
    1. content.stability >= stability_threshold
    2. content.priority >= priority_threshold
    3. workspace.has_capacity()

    "是否进入语言报告"不能作为广播是否发生的唯一定义。
    """

    def __init__(
        self,
        max_capacity: int = 10,
        stability_threshold: float = 0.3,
        priority_threshold: float = 0.2,
    ) -> None:
        self.max_capacity = max_capacity
        self.stability_threshold = stability_threshold
        self.priority_threshold = priority_threshold

    async def broadcast(
        self,
        state: ConsciousState,
        selected: list[ContentCandidate],
    ) -> list[BroadcastContent]:
        """将选中的内容广播到工作空间。

        只有通过门控的内容才能影响:
        - 工作记忆
        - 规划
        - 行动
        - 语言生成
        - 情景记忆
        - 自我模型
        - 目标更新
        """
        broadcast_items: list[BroadcastContent] = []

        for content in selected:
            # 门控检查
            if content.stability < self.stability_threshold:
                continue
            if content.priority < self.priority_threshold:
                continue
            if len(broadcast_items) >= self.max_capacity:
                break

            bc = BroadcastContent(content=content)
            broadcast_items.append(bc)

        return broadcast_items


# ═══════════════════════════════════════════════════════════════
# Metacognition — 元认知评估
# ═══════════════════════════════════════════════════════════════

class Metacognition:
    """元认知 — 评估自身认知过程的置信度、不确定性和反思。

    核心功能:
    - 置信度评估：系统对自己的输出有多大把握
    - 不确定性追踪：哪些领域是已知未知
    - 反思循环：是否需要进入 REFLECTING 模式
    """

    async def evaluate(
        self,
        state: ConsciousState,
        broadcast: list[BroadcastContent],
        self_delta: dict | None = None,
    ) -> dict[str, Any]:
        """元认知评估。

        Returns:
            {
                "confidence": {...},
                "uncertainty": {...},
                "affect": {...},
                "should_reflect": bool,
                "reflection_focus": str | None,
            }
        """
        confidence: dict[str, float] = {}
        uncertainty: dict[str, float] = {}
        affect: dict[str, Any] = {}

        # 基础置信度
        if broadcast:
            # 有内容广播 → 系统在活跃处理
            confidence["system_active"] = 0.9
            uncertainty["system_stuck"] = 0.1
        else:
            confidence["system_active"] = 0.3
            uncertainty["system_stuck"] = 0.7

        # 身体状态影响
        if state.body.error_rate > 0.1:
            confidence["body_healthy"] = 0.4
            uncertainty["body_issues"] = 0.8
            affect["concern"] = "elevated_error_rate"

        # 是否需要反思
        should_reflect = (
            len(broadcast) == 0
            and state.tick % 100 == 0  # 每100个tick反思一次
        )

        return {
            "confidence": confidence,
            "uncertainty": uncertainty,
            "affect": affect,
            "should_reflect": should_reflect,
            "reflection_focus": "system_state" if should_reflect else None,
        }


# ═══════════════════════════════════════════════════════════════
# DecisionEngine — 决策引擎
# ═══════════════════════════════════════════════════════════════

class DecisionEngine:
    """决策引擎 — 从元认知评估和广播内容中做出决策。"""

    async def decide(
        self,
        state: ConsciousState,
        evaluation: dict,
    ) -> dict[str, Any]:
        """做出决策。

        Returns:
            {
                "requires_action": bool,
                "intent": {"description": str, "priority": float} | None,
                "reasoning": str,
                "suggested_mode": RuntimeMode | None,
            }
        """
        requires_action = False
        intent = None
        reasoning_parts = []

        # 如果有风险警报 → 需要行动
        for bc in state.workspace:
            if bc.content.content_type == "risk_alert":
                requires_action = True
                intent = {
                    "description": f"Address risk: {bc.content.summary}",
                    "priority": 0.9,
                }
                reasoning_parts.append("viability risk detected")
                break

        # 如果有响应候选 → 需要行动
        if not requires_action:
            for bc in state.workspace:
                if bc.content.content_type == "response":
                    requires_action = True
                    intent = {
                        "description": bc.content.summary,
                        "priority": bc.content.priority,
                    }
                    reasoning_parts.append("user message requires response")
                    break

        # 如果需要反思
        suggested_mode = None
        if evaluation.get("should_reflect"):
            suggested_mode = RuntimeMode.REFLECTING
            reasoning_parts.append("periodic reflection")

        return {
            "requires_action": requires_action,
            "intent": intent,
            "reasoning": "; ".join(reasoning_parts) if reasoning_parts else "no action needed",
            "suggested_mode": suggested_mode,
        }


# ═══════════════════════════════════════════════════════════════
# PipelineCognitionLoop — 分阶段认知循环
# ═══════════════════════════════════════════════════════════════

class PipelineCognitionLoop:
    """MVSC 统一认知循环实现。

    用法:
        loop = PipelineCognitionLoop(
            state_repo=...,
            event_bus=...,
            world_model=...,
            body_model=...,
            content_engine=ContentEngine(),
            attention=Attention(),
            workspace=Workspace(),
            self_model=...,
            metacognition=Metacognition(),
            decision_engine=DecisionEngine(),
            planner=...,
            agent_os=...,
            verifier=...,
            memory=...,
        )

        await loop.run_once(event)
    """

    def __init__(
        self,
        state_repo: Any = None,
        event_bus: Any = None,
        world_model: Any = None,
        body_model: Any = None,
        content_engine: ContentEngine | None = None,
        attention: Attention | None = None,
        workspace: Workspace | None = None,
        self_model: Any = None,
        metacognition: Metacognition | None = None,
        decision_engine: DecisionEngine | None = None,
        planner: Any = None,
        agent_os: Any = None,
        verifier: Any = None,
        memory: Any = None,
        feature_flags: dict[str, bool] | None = None,
    ) -> None:
        self.state_repo = state_repo
        self.event_bus = event_bus
        self.world_model = world_model
        self.body_model = body_model
        self.content_engine = content_engine or ContentEngine()
        self.attention = attention or Attention()
        self.workspace = workspace or Workspace()
        self.self_model = self_model
        self.metacognition = metacognition or Metacognition()
        self.decision_engine = decision_engine or DecisionEngine()
        self.planner = planner
        self.agent_os = agent_os
        self.verifier = verifier
        self.memory = memory
        self.feature_flags = feature_flags or {
            "recurrent_content": True,
            "global_workspace": True,
            "self_model": True,
            "value_model": True,
            "episodic_memory": True,
            "narrative_identity": True,
            "metacognition": True,
        }
        self._phase_timings: dict[str, float] = {}

    async def run_once(self, event: EventEnvelope) -> ConsciousState:
        """执行一个完整的认知循环。

        Args:
            event: 触发此循环的事件

        Returns:
            更新后的 ConsciousState
        """
        t_start = time.perf_counter()

        # 加载当前状态
        state = await self._load_state(event.subject_id)
        state.cognition_phase = CognitionPhase.PERCEIVE
        emitted_events: list[EventEnvelope] = []

        # ── Phase 1: PERCEIVE ──
        t0 = time.perf_counter()
        # 感知已在事件接收时完成，此处做标准化
        self._record_phase("perceive", t0)

        # ── Phase 2: UPDATE_WORLD ──
        t0 = time.perf_counter()
        world_delta = {}
        if self.world_model and self.feature_flags.get("value_model", True):
            world_delta = await self._update_world(state, event)
        state.cognition_phase = CognitionPhase.UPDATE_WORLD
        self._record_phase("update_world", t0)

        # ── Phase 3: UPDATE_BODY ──
        t0 = time.perf_counter()
        body_delta = {}
        if self.body_model:
            body_delta = await self._update_body(state, event)
        state.cognition_phase = CognitionPhase.UPDATE_BODY
        self._record_phase("update_body", t0)

        # ── Phase 4: GENERATE_CONTENT ──
        t0 = time.perf_counter()
        candidates: list[ContentCandidate] = []
        if self.feature_flags.get("recurrent_content", True):
            candidates = await self.content_engine.generate(
                state, event, world_delta, body_delta,
            )
        state.cognition_phase = CognitionPhase.GENERATE_CONTENT
        state.active_contents = candidates
        self._record_phase("generate_content", t0)

        # ── Phase 5: SELECT_ATTENTION ──
        t0 = time.perf_counter()
        selected = await self.attention.select(state, candidates)
        state.cognition_phase = CognitionPhase.SELECT_ATTENTION
        self._record_phase("select_attention", t0)

        # ── Phase 6: BROADCAST ──
        t0 = time.perf_counter()
        broadcast: list[BroadcastContent] = []
        if self.feature_flags.get("global_workspace", True):
            broadcast = await self.workspace.broadcast(state, selected)
        state.cognition_phase = CognitionPhase.BROADCAST
        state.workspace = broadcast
        self._record_phase("broadcast", t0)

        # ── Phase 7: ATTRIBUTE_SELF ──
        t0 = time.perf_counter()
        self_delta = {}
        if self.self_model and self.feature_flags.get("self_model", True):
            self_delta = await self._attribute_self(state, event, broadcast)
        state.cognition_phase = CognitionPhase.ATTRIBUTE_SELF
        self._record_phase("attribute_self", t0)

        # ── Phase 8: EVALUATE ──
        t0 = time.perf_counter()
        evaluation = {}
        if self.feature_flags.get("metacognition", True):
            evaluation = await self.metacognition.evaluate(
                state, broadcast, self_delta,
            )
        state.cognition_phase = CognitionPhase.EVALUATE
        self._record_phase("evaluate", t0)

        # ── Phase 9: DECIDE ──
        t0 = time.perf_counter()
        decision = await self.decision_engine.decide(state, evaluation)
        state.cognition_phase = CognitionPhase.DECIDE
        self._record_phase("decide", t0)

        # ── Phase 10: PLAN ──
        t0 = time.perf_counter()
        plan = None
        if decision["requires_action"] and self.planner:
            plan = await self._create_plan(state, decision)
            state.current_plan = plan
        state.cognition_phase = CognitionPhase.PLAN
        self._record_phase("plan", t0)

        # ── Phase 11: ACT ──
        t0 = time.perf_counter()
        if decision["requires_action"] and self.agent_os:
            action_events = await self._execute_action(state, decision, plan)
            emitted_events.extend(action_events)
        state.cognition_phase = CognitionPhase.ACT
        self._record_phase("act", t0)

        # ── Phase 12: VERIFY ──
        t0 = time.perf_counter()
        if self.verifier and emitted_events:
            verification_events = await self._verify_actions(state, decision, emitted_events)
            emitted_events.extend(verification_events)
        state.cognition_phase = CognitionPhase.VERIFY
        self._record_phase("verify", t0)

        # ── Phase 13: CONSOLIDATE ──
        t0 = time.perf_counter()
        memory_events: list[EventEnvelope] = []
        if self.memory and self.feature_flags.get("episodic_memory", True):
            memory_events = await self._consolidate_memory(
                state, event, broadcast, evaluation, emitted_events,
            )
        state.cognition_phase = CognitionPhase.CONSOLIDATE
        self._record_phase("consolidate", t0)

        # ── 应用增量更新 ──
        new_state = state.apply(
            world_delta=world_delta,
            body_delta=body_delta,
            self_delta=self_delta,
            broadcast=broadcast,
            evaluation=evaluation,
            action_events=emitted_events,
            memory_events=memory_events,
        )

        # ── 持久化 ──
        if self.state_repo:
            await self.state_repo.commit(
                previous_version=state.version,
                new_state=new_state,
                events=[event, *emitted_events, *memory_events],
            )

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "cognition pipeline complete tick=%d phases=%s total_ms=%.1f",
            new_state.tick,
            list(self._phase_timings.keys()),
            total_ms,
        )

        return new_state

    # ── 阶段实现（可被子类重写以适配现有组件）──

    async def _load_state(self, subject_id: str) -> ConsciousState:
        if self.state_repo:
            return await self.state_repo.load(subject_id)
        return ConsciousState(subject_id=subject_id)

    async def _update_world(self, state: ConsciousState, event: EventEnvelope) -> dict:
        """更新世界模型。子类可重写以使用现有 WorldModelGraph。"""
        return {}

    async def _update_body(self, state: ConsciousState, event: EventEnvelope) -> dict:
        """更新身体模型。子类可重写。"""
        return {}

    async def _attribute_self(
        self, state: ConsciousState, event: EventEnvelope,
        broadcast: list[BroadcastContent],
    ) -> dict:
        """自我归属。子类可重写以使用现有 SelfModelStore。"""
        return {}

    async def _create_plan(self, state: ConsciousState, decision: dict) -> Any:
        """创建执行计划。子类可重写以使用现有 Planner。"""
        return None

    async def _execute_action(
        self, state: ConsciousState, decision: dict, plan: Any,
    ) -> list[EventEnvelope]:
        """执行行动。子类可重写以使用现有 AgentOS。"""
        return []

    async def _verify_actions(
        self, state: ConsciousState, decision: dict,
        action_events: list[EventEnvelope],
    ) -> list[EventEnvelope]:
        """验证行动结果。子类可重写。"""
        return []

    async def _consolidate_memory(
        self, state: ConsciousState, event: EventEnvelope,
        broadcast: list[BroadcastContent], evaluation: dict,
        action_events: list[EventEnvelope],
    ) -> list[EventEnvelope]:
        """记忆整合。子类可重写以使用现有 TieredMemoryManager。"""
        return []

    def _record_phase(self, phase: str, t0: float) -> None:
        self._phase_timings[phase] = (time.perf_counter() - t0) * 1000

    @property
    def phase_timings(self) -> dict[str, float]:
        return dict(self._phase_timings)
