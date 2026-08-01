"""PlannerDAG — MVSC DAG 计划器。

从 Intent 生成 Plan (PlanStep DAG)。

支持:
- 依赖关系: 步骤可以有前置步骤
- 前置/后置条件: 步骤执行前/后的检查
- 重试策略: 每个步骤可配置重试
- 风险等级: low/medium/high/critical
"""

from __future__ import annotations

import logging
from typing import Any

from packages.contracts.protocols import Intent, Decision
from packages.contracts.state import ConsciousState, Plan, PlanStep

logger = logging.getLogger("eva.agentos.planner_dag")


class PlannerDAG:
    """从意图生成 DAG 执行计划。

    Usage::

        planner = PlannerDAG()
        plan = await planner.create(state, decision)
    """

    def __init__(self) -> None:
        self._plan_count = 0

    async def create(
        self,
        state: ConsciousState | None = None,
        decision: Decision | None = None,
        intent: Intent | None = None,
    ) -> Plan:
        """创建执行计划。

        从 intent 生成 PlanStep DAG。
        """
        self._plan_count += 1

        # 提取 intent
        if decision and decision.intent:
            intent = decision.intent
        if intent is None:
            intent = Intent(intent_id="default", description="No intent")

        plan_id = f"plan_{self._plan_count:04d}"

        # ── 根据 intent 生成步骤 ──
        steps = self._generate_steps(intent, plan_id)

        return Plan(
            plan_id=plan_id,
            goal_id=intent.goal_id or intent.intent_id,
            steps=steps,
            status="pending",
        )

    def _generate_steps(self, intent: Intent, plan_id: str) -> list[PlanStep]:
        """根据意图生成计划步骤。"""
        desc = intent.description.lower()
        steps: list[PlanStep] = []

        # ── 模式匹配生成步骤 ──
        if any(w in desc for w in ("search", "find", "look")):
            # 搜索任务: search → analyze → respond
            s1 = PlanStep(
                step_id=f"{plan_id}_s1",
                agent_type="search_agent",
                tool_id="search_files",
                input_schema={"query": intent.description},
                expected_output_schema={"results": "list"},
                risk_level="low",
            )
            s2 = PlanStep(
                step_id=f"{plan_id}_s2",
                dependencies=[s1.step_id],
                agent_type="chat_agent",
                input_schema={"results_from_search": "list"},
                expected_output_schema={"response": "str"},
                risk_level="low",
            )
            steps = [s1, s2]

        elif any(w in desc for w in ("code", "file", "read", "analyze")):
            # 代码/文件任务: read → analyze → respond
            s1 = PlanStep(
                step_id=f"{plan_id}_s1",
                agent_type="coding_agent",
                tool_id="read_file",
                input_schema={"path": "str"},
                expected_output_schema={"content": "str"},
                risk_level="low",
            )
            s2 = PlanStep(
                step_id=f"{plan_id}_s2",
                dependencies=[s1.step_id],
                agent_type="chat_agent",
                input_schema={"content": "str"},
                expected_output_schema={"response": "str"},
                risk_level="low",
            )
            steps = [s1, s2]

        else:
            # 默认: 单步聊天
            s1 = PlanStep(
                step_id=f"{plan_id}_s1",
                agent_type="chat_agent",
                input_schema={"text": intent.description},
                expected_output_schema={"response": "str"},
                risk_level="low",
            )
            steps = [s1]

        # ── 应用约束 ──
        for constraint in intent.constraints:
            if constraint == "time_sensitive":
                for s in steps:
                    s.retry_policy = {"max_retries": 1, "backoff_sec": 0.5}
            elif constraint == "safety_critical":
                for s in steps:
                    s.risk_level = "high"
            elif constraint == "high_accuracy":
                for s in steps:
                    s.retry_policy = {"max_retries": 3, "backoff_sec": 1.0}

        return steps

    @property
    def stats(self) -> dict[str, Any]:
        return {"plan_count": self._plan_count}
