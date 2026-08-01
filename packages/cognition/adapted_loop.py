"""AdaptedCognitionLoop — 将 PipelineCognitionLoop 接入现有 EVA 后端。

继承 MVSC 的 PipelineCognitionLoop，重写关键阶段方法以使用:
- WorldModelGraph (world/world_model.py)
- TieredMemoryManager (memory/tiered_store.py)
- 现有 AgentOS (agent_os/, agents/, core/executor.py)
- SelfModelStore (persona/self_model_store.py)
- PredictionTracker (core/prediction.py)

这是新旧代码的关键桥梁 — 所有现有后端保持不变，
只是通过此适配器以 MVSC Pipeline 的方式被调用。
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import AgentTask
from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import (
    BroadcastContent,
    ConsciousState,
)
from packages.cognition.loop import (
    Attention,
    ContentEngine,
    DecisionEngine,
    Metacognition,
    PipelineCognitionLoop,
    Workspace,
)
from packages.kernel.state_bridge import (
    sync_system_state,
)

logger = logging.getLogger("eva.cognition.adapted")


class AdaptedCognitionLoop(PipelineCognitionLoop):
    """接入现有 EVA 后端的 MVSC 认知循环。

    通过重写 _update_world, _update_body, _execute_action,
    _consolidate_memory 等方法，将 MVSC 的 13 阶段 Pipeline
    连接到现有的 WorldModelGraph, TieredMemoryManager, AgentOS 等。

    Usage (in bootstrap)::

        adapted = AdaptedCognitionLoop(
            container=app_container,
            feature_flags={"global_workspace": True, ...},
        )
        await adapted.run_once(event_envelope)
    """

    def __init__(
        self,
        container: Any = None,  # AppContainer
        intent_parser: Any = None,
        planner_dag: Any = None,
        verifier: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._container = container
        self._intent_parser = intent_parser
        self._planner_dag = planner_dag
        self._verifier = verifier

    # ── Phase 2: UPDATE_WORLD ──────────────────────────────────

    async def _update_world(
        self, state: ConsciousState, event: EventEnvelope,
    ) -> dict:
        """使用现有 WorldModelGraph 更新世界模型。

        只通过返回 delta 来影响状态，不直接修改 container。
        状态同步由 run_once_and_sync() 统一完成。
        """
        if self._container is None:
            return {}

        wm = self._container.world_model
        delta: dict[str, Any] = {}

        if event.event_type == EventFamily.PERCEPTION.USER_MESSAGE:
            text = event.payload.get("text", "")
            wm.apply_user_message(text)
            delta["focus"] = wm.focus
            delta["mode"] = wm.mode
            delta["active_tasks"] = wm.active_tasks

        elif event.event_type == EventFamily.LIFECYCLE.MAINTENANCE_STARTED:
            delta["focus"] = wm.focus
            delta["active_tasks"] = wm.active_tasks

        return delta

    # ── Phase 3: UPDATE_BODY ───────────────────────────────────

    async def _update_body(
        self, state: ConsciousState, event: EventEnvelope,
    ) -> dict:
        """从现有运行时指标更新身体状态。"""
        if self._container is None:
            return {}

        loop = self._container.loop
        delta: dict[str, Any] = {}

        # 从现有 cognition loop 获取错误计数
        if hasattr(loop, "_consecutive_errors"):
            delta["consecutive_failures"] = loop._consecutive_errors
            delta["error_rate"] = min(1.0, loop._consecutive_errors / 100.0)

        # 从 event bus 获取队列深度
        if self._container.event_bus:
            delta["cpu_load"] = min(100.0, self._container.event_bus.size() * 2)

        return delta

    # ── Phase 7: ATTRIBUTE_SELF ────────────────────────────────

    async def _attribute_self(
        self, state: ConsciousState, event: EventEnvelope,
        broadcast: list[BroadcastContent],
    ) -> dict:
        """使用现有 SelfModelStore + PredictionTracker 做自我归属。"""
        if self._container is None:
            return {}

        delta: dict[str, Any] = {}

        # 记录预测误差
        tracker = self._container.prediction_tracker
        if tracker and state.world.get("focus"):
            prev_focus = state.world.get("focus", "")
            if prev_focus and broadcast:
                expected = f"response about {prev_focus[:60]}"
                actual = broadcast[0].content.summary[:200] if broadcast else ""
                rec = tracker.record(focus=prev_focus, expected=expected, actual=actual)

                # 写入自我模型
                store = self._container.self_model_store
                if store:
                    sm = self._container.self_model
                    if sm is not None:
                        store.record_prediction_error(sm, error=rec.error, focus=prev_focus)
                        if rec.error > 0.6:
                            store.record_perturbation(
                                sm,
                                cause=f"high prediction error ({rec.error:.2f})",
                                delta_magnitude=rec.error,
                                affected_fields=["world.focus", "prediction"],
                            )
                        delta["prediction_error"] = rec.error

        return delta

    # ── Phase 11: ACT ──────────────────────────────────────────

    async def _execute_action(
        self, state: ConsciousState, decision: dict, plan: Any,
    ) -> list[EventEnvelope]:
        """使用 IntentParser → PlannerDAG → AgentOS → Verifier 全链路执行。

        将 MVSC 决策转换为完整的行动流水线。
        """
        if self._container is None or not decision.get("requires_action"):
            return []

        intent_dict = decision.get("intent", {})
        events: list[EventEnvelope] = []

        try:
            # ── Step 1: Parse intent ──
            intent = None
            if self._intent_parser:
                # Build a synthetic event from the decision
                synthetic_event = EventEnvelope(
                    event_type=EventFamily.PERCEPTION.USER_MESSAGE,
                    source="decision_engine",
                    payload={"text": intent_dict.get("description", "")},
                )
                intent = await self._intent_parser.parse(synthetic_event, state)

            if intent is None:
                from packages.contracts.protocols import Intent as IntentModel
                intent = IntentModel(
                    intent_id=f"int_{state.tick}",
                    description=intent_dict.get("description", ""),
                    priority=intent_dict.get("priority", 0.5),
                )

            # ── Step 2: Create plan ──
            if self._planner_dag and plan is None:
                plan = await self._planner_dag.create(state=state, intent=intent)
                events.append(EventEnvelope(
                    event_type=EventFamily.PLAN.CREATED,
                    source="adapted_loop",
                    payload={"plan_id": plan.plan_id, "steps": len(plan.steps)},
                ))

            # ── Step 3: Execute each plan step ──
            router = self._container.agent_router
            orchestrator = self._container.orchestrator

            for step in (plan.steps if plan else []):
                # Build AgentTask from PlanStep
                task = AgentTask(
                    kind="chat" if step.agent_type == "chat_agent" else step.agent_type.replace("_agent", ""),
                    payload={
                        "text": intent.description,
                        "context": {
                            "step_id": step.step_id,
                            "expected_output": step.expected_output_schema,
                        },
                    },
                )

                selected_agent = router.route(step.agent_type, task)
                result, duration_ms = orchestrator.execute(selected_agent, task)

                events.append(EventEnvelope(
                    event_type=EventFamily.ACTION.AGENT_INVOKED,
                    source="adapted_loop",
                    payload={"agent": selected_agent, "step": step.step_id, "duration_ms": duration_ms},
                ))

                # ── Step 4: Verify ──
                if self._verifier:
                    from packages.contracts.protocols import ToolResult
                    vr = await self._verifier.verify(
                        intent=intent,
                        plan=plan,
                        result=ToolResult(
                            ok=result.ok,
                            data={"reply": result.content, "summary": result.summary},
                            error=None if result.ok else result.summary,
                            duration_ms=duration_ms,
                        ),
                    )
                    verification_events = self._verifier.to_events(vr, events[-1].event_id)
                    events.extend(verification_events)

                    if not vr.passed:
                        logger.warning("verification failed for step %s: %s", step.step_id, vr.summary)

                events.append(EventEnvelope(
                    event_type=EventFamily.ACTION.AGENT_COMPLETED,
                    source="adapted_loop",
                    payload={"agent": selected_agent, "ok": result.ok, "summary": result.summary},
                ))

            # Fallback: no plan steps → direct agent call
            if not plan or not plan.steps:
                task = AgentTask(
                    kind="chat",
                    payload={"text": intent.description, "context": {}},
                )
                selected_agent = router.route("chat_agent", task)
                result, duration_ms = orchestrator.execute(selected_agent, task)
                events.append(EventEnvelope(
                    event_type=EventFamily.ACTION.AGENT_INVOKED,
                    source="adapted_loop",
                    payload={"agent": selected_agent, "duration_ms": duration_ms},
                ))
                events.append(EventEnvelope(
                    event_type=EventFamily.ACTION.AGENT_COMPLETED,
                    source="adapted_loop",
                    payload={"agent": selected_agent, "ok": result.ok, "summary": result.summary},
                ))

            # Update world model with final result
            if self._container.world_model:
                wm = self._container.world_model
                final_result = result if 'result' in dir() else None
                if final_result:
                    wm.apply_agent_result(
                        reply=final_result.content,
                        selected_agent=selected_agent if 'selected_agent' in dir() else "chat_agent",
                        loop_id=f"mvsc_{state.tick}",
                    )

        except Exception:
            logger.exception("agent execution failed in adapted loop")
            events.append(EventEnvelope(
                event_type=EventFamily.ACTION.TOOL_FAILED,
                source="adapted_loop",
                payload={"error": "agent execution failed"},
            ))

        return events

    # ── Phase 13: CONSOLIDATE ──────────────────────────────────

    async def _consolidate_memory(
        self, state: ConsciousState, event: EventEnvelope,
        broadcast: list[BroadcastContent], evaluation: dict,
        action_events: list[EventEnvelope],
    ) -> list[EventEnvelope]:
        """使用现有 TieredMemoryManager 整合记忆。"""
        if self._container is None:
            return []

        tm = self._container.tiered_memory
        if tm is None:
            return []

        events: list[EventEnvelope] = []

        try:
            for bc in broadcast:
                importance = bc.content.priority
                tm.ingest(
                    bc.content.summary,
                    importance=importance,
                    source=bc.content.source_module,
                    category=bc.content.content_type,
                    source_event_id=event.event_id,
                    self_model_delta=evaluation.get("self_model_delta", 0.0),
                    prediction_error=evaluation.get("prediction_error", 0.0),
                )
                events.append(EventEnvelope(
                    event_type=EventFamily.MEMORY.EPISODE_COMMITTED,
                    source="adapted_loop",
                    payload={
                        "content_id": bc.content.content_id,
                        "importance": importance,
                    },
                ))

            # Memory governor maintenance on consolidation ticks
            if state.tick % 100 == 0 and self._container.memory_governor:
                self._container.memory_governor.maintenance()

        except Exception:
            logger.exception("memory consolidation failed")

        return events

    # ── Convenience: sync-back wrapper ─────────────────────────

    async def run_once_and_sync(self, event: EventEnvelope) -> ConsciousState:
        """执行一次认知循环并同步回 system_state。

        这是与现有 bootstrap 集成的主要入口。
        """
        new_state = await self.run_once(event)

        # 同步回 system_state 供现有 API 使用
        if self._container and self._container.system_state:
            sync_system_state(self._container.system_state, new_state)

        return new_state


# ═══════════════════════════════════════════════════════════════
# Bootstrap 集成 helper
# ═══════════════════════════════════════════════════════════════

def create_adapted_loop(container: Any, feature_flags: dict[str, bool] | None = None) -> AdaptedCognitionLoop:
    """从 AppContainer 创建 AdaptedCognitionLoop。

    使用容器中的所有现有后端组件 + 新的 MVSC AgentOS 组件。
    """
    from packages.agentos.intent_parser import IntentParser
    from packages.agentos.planner_dag import PlannerDAG
    from packages.agentos.verifier import Verifier

    return AdaptedCognitionLoop(
        container=container,
        event_bus=container.event_bus,
        world_model=container.world_model,
        self_model=container.self_model,
        planner=container.planner,
        agent_os=container.orchestrator,
        memory=container.tiered_memory,
        content_engine=ContentEngine(),
        attention=Attention(),
        workspace=Workspace(),
        metacognition=Metacognition(),
        decision_engine=DecisionEngine(),
        intent_parser=IntentParser(),
        planner_dag=PlannerDAG(),
        verifier=Verifier(),
        feature_flags=feature_flags,
    )
