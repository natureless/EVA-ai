"""EVA-MVSC 状态模型 — ConsciousState 和子状态定义。

统一状态表达为:
    X_t = (W_t, B_t, S_t, C_t, G_t, M_t, V_t, P_t, R_t, L_t)

其中:
    W_t = World Model     B_t = Body State
    S_t = Self Model      C_t = Active Contents
    G_t = Goals           M_t = Memory Context
    V_t = Value/Affect    P_t = Plan
    R_t = Relations       L_t = Lifecycle
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# 运行模式 & 认知阶段
# ═══════════════════════════════════════════════════════════════

class RuntimeMode(str, Enum):
    """EVA Kernel 的8种运行模式。

    与旧 PolicyEngine 的4态 (Dormant/Commanded/Supervised/Quarantined) 的关系:
    - BOOTING: 新增，系统启动中
    - ACTIVE: 对应旧 Dormant + Commanded 的正常运行状态
    - REFLECTING: 新增，元认知反思模式
    - CONSOLIDATING: 新增，记忆整合模式
    - DEGRADED: 新增，降级运行（部分模块不可用）
    - SUSPENDED: 新增，暂停（保持状态但不处理新事件）
    - RECOVERING: 新增，从快照恢复中
    - SHUTTING_DOWN: 新增，安全关闭中
    """
    BOOTING = "booting"
    ACTIVE = "active"
    REFLECTING = "reflecting"
    CONSOLIDATING = "consolidating"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    RECOVERING = "recovering"
    SHUTTING_DOWN = "shutting_down"


class CognitionPhase(str, Enum):
    """认知循环的13个阶段。"""
    PERCEIVE = "perceive"
    UPDATE_WORLD = "update_world"
    UPDATE_BODY = "update_body"
    GENERATE_CONTENT = "generate_content"
    SELECT_ATTENTION = "select_attention"
    BROADCAST = "broadcast"
    ATTRIBUTE_SELF = "attribute_self"
    EVALUATE = "evaluate"
    DECIDE = "decide"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    CONSOLIDATE = "consolidate"


# ═══════════════════════════════════════════════════════════════
# Body State — 数字身体
# ═══════════════════════════════════════════════════════════════

class BodyState(BaseModel):
    """数字身体状态 — EVA的"身体边界"。

    这是MVSC框架的核心新增概念。数字身体不是物理身体，
    而是进程、存储、网络、预算等数字资源的统一表示。
    """

    cpu_load: float = Field(default=0.0, ge=0.0, le=100.0)
    memory_usage: float = Field(default=0.0, ge=0.0, le=100.0)
    disk_usage: float = Field(default=0.0, ge=0.0, le=100.0)
    network_health: float = Field(default=1.0, ge=0.0, le=1.0)

    token_budget: int = Field(default=1000, ge=0)
    cost_budget: float = Field(default=10.0, ge=0.0)

    process_health: float = Field(default=1.0, ge=0.0, le=1.0)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: float = Field(default=0.0, ge=0.0)

    permission_scope: set[str] = Field(default_factory=set)
    active_processes: list[str] = Field(default_factory=list)

    last_heartbeat: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)

    class Config:
        # set 不能直接用 JSON，需要转换
        json_encoders = {set: list}


class ViabilityBounds(BaseModel):
    """可生存域 — 超出这些边界意味着系统进入生存风险状态。

    价值系统的根基：不是"我想要什么"，而是"我需要什么来维持存在"。
    """

    max_memory_usage: float = Field(default=90.0, ge=0.0, le=100.0)
    max_error_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    max_cost_per_hour: float = Field(default=5.0, ge=0.0)
    min_disk_free: float = Field(default=10.0, ge=0.0, le=100.0)
    min_process_health: float = Field(default=0.5, ge=0.0, le=1.0)
    max_consecutive_failures: int = Field(default=5, ge=1)

    def check(self, body: BodyState) -> list[str]:
        """检查身体状态是否在可生存域内，返回违规项列表。"""
        violations: list[str] = []
        if body.memory_usage > self.max_memory_usage:
            violations.append(f"memory_usage {body.memory_usage}% > {self.max_memory_usage}%")
        if body.error_rate > self.max_error_rate:
            violations.append(f"error_rate {body.error_rate} > {self.max_error_rate}")
        if body.consecutive_failures >= self.max_consecutive_failures:
            violations.append(f"consecutive_failures {body.consecutive_failures} >= {self.max_consecutive_failures}")
        return violations


# ═══════════════════════════════════════════════════════════════
# 内容与注意力
# ═══════════════════════════════════════════════════════════════

class ContentCandidate(BaseModel):
    """候选内容 — 由 ContentEngine 生成，经过 Attention 竞争筛选。

    每个候选必须保留各分量得分，确保事后可解释为什么它获得注意。
    """

    content_id: str = Field(default_factory=lambda: f"cnt_{uuid4().hex[:8]}")
    content_type: str  # "thought", "plan_suggestion", "risk_alert", "reflection", ...
    summary: str
    source_module: str = "unknown"  # 哪个模块生成了此候选

    # ── 注意力分量（可解释性关键）──
    salience: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    viability_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    deadline_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_impact: float = Field(default=0.0, ge=0.0, le=1.0)
    inhibition: float = Field(default=0.0, ge=0.0, le=1.0)

    # ── 元数据 ──
    stability: float = Field(default=0.0, ge=0.0, le=1.0)  # 递归稳定后的稳定性
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    recurrence_count: int = Field(default=0)  # 被重复生成的次数

    @property
    def priority(self) -> float:
        """可解释的注意力优先级。

        Priority = w_s*Salience + w_g*GoalRelevance + w_v*ViabilityRisk
                 + w_n*Novelty + w_u*Uncertainty + w_d*Deadline
                 + w_e*ExpectedImpact - w_i*Inhibition
        """
        return (
            0.20 * self.salience
            + 0.20 * self.goal_relevance
            + 0.25 * self.viability_risk  # 生存风险权重最高
            + 0.10 * self.novelty
            + 0.10 * self.uncertainty
            + 0.05 * self.deadline_pressure
            + 0.10 * self.expected_impact
            - 0.10 * self.inhibition
        )


class BroadcastContent(BaseModel):
    """广播内容 — 通过 Workspace 广播到其他模块的内容。"""

    content: ContentCandidate
    broadcast_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    affected_modules: list[str] = Field(default_factory=list)  # 哪些模块接收了此广播
    ttl_seconds: float = Field(default=30.0)  # 广播有效期


# ═══════════════════════════════════════════════════════════════
# 目标与计划
# ═══════════════════════════════════════════════════════════════

class Goal(BaseModel):
    """结构化目标。"""

    goal_id: str = Field(default_factory=lambda: f"goal_{uuid4().hex[:8]}")
    description: str
    source: str  # "user", "system", "derived"
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    status: str = "active"  # active | completed | failed | abandoned

    success_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline: datetime | None = None
    parent_goal_id: str | None = None

    authority_level: str = "user"  # user | system | agent


class Commitment(BaseModel):
    """承诺 — 系统对目标的责任绑定。"""

    commitment_id: str = Field(default_factory=lambda: f"cmt_{uuid4().hex[:8]}")
    goal_id: str
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resources_allocated: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"  # active | fulfilled | breached


class PlanStep(BaseModel):
    """计划步骤 — DAG中的一个节点。"""

    step_id: str = Field(default_factory=lambda: f"step_{uuid4().hex[:8]}")
    dependencies: list[str] = Field(default_factory=list)  # 前置步骤ID列表
    agent_type: str = "chat_agent"
    tool_id: str | None = None

    input_schema: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)

    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)

    retry_policy: dict[str, Any] = Field(default_factory=lambda: {"max_retries": 2, "backoff_sec": 1.0})
    risk_level: str = "low"  # low | medium | high | critical


class Plan(BaseModel):
    """执行计划 — 步骤DAG。"""

    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex[:8]}")
    goal_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"  # pending | executing | completed | failed
    current_step_index: int = 0


# ═══════════════════════════════════════════════════════════════
# ConsciousState — 统一系统状态
# ═══════════════════════════════════════════════════════════════

class ConsciousState(BaseModel):
    """EVA-MVSC 的统一系统状态。

    这是权威状态的唯一表示。所有模块通过此结构读取和更新状态。
    版本号用于乐观锁并发控制。
    """

    # ── 标识 ──
    subject_id: str = "eva-001"
    version: int = 0  # 乐观锁版本号，每次提交+1
    tick: int = 0  # 认知循环计数

    # ── 模式 ──
    runtime_mode: RuntimeMode = RuntimeMode.BOOTING
    cognition_phase: CognitionPhase = CognitionPhase.PERCEIVE

    # ── W_t: World Model ──
    world: dict[str, Any] = Field(default_factory=dict)

    # ── B_t: Body State ──
    body: BodyState = Field(default_factory=BodyState)

    # ── S_t: Self Model (6子模型摘要) ──
    self_model: dict[str, Any] = Field(default_factory=dict)

    # ── C_t: Active Contents ──
    active_contents: list[ContentCandidate] = Field(default_factory=list)
    workspace: list[BroadcastContent] = Field(default_factory=list)

    # ── G_t: Goals & Commitments ──
    goals: list[Goal] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)

    # ── P_t: Current Plan ──
    current_plan: Plan | None = None

    # ── V_t: Affect & Value ──
    affect: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    uncertainty: dict[str, float] = Field(default_factory=dict)

    # ── M_t: Memory Context ──
    working_memory: list[str] = Field(default_factory=list)
    narrative_context: list[str] = Field(default_factory=list)

    # ── R_t: Relations ──
    relations: dict[str, Any] = Field(default_factory=dict)

    # ── L_t: Lifecycle ──
    health: dict[str, Any] = Field(default_factory=dict)
    integrity_hash: str = ""

    # ── 实验性 ──
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    def compute_integrity_hash(self) -> str:
        """计算状态完整性哈希（用于验证状态一致性）。"""
        import hashlib
        import json

        # 排除自身哈希字段
        data = self.model_dump(mode="json", exclude={"integrity_hash"})
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def apply(
        self,
        world_delta: dict | None = None,
        body_delta: dict | None = None,
        self_delta: dict | None = None,
        broadcast: list[BroadcastContent] | None = None,
        evaluation: dict | None = None,
        action_events: list | None = None,
        memory_events: list | None = None,
    ) -> "ConsciousState":
        """应用增量更新，返回新版本状态。"""
        new_state = self.model_copy(deep=True)
        new_state.version += 1
        new_state.tick += 1

        if world_delta:
            new_state.world.update(world_delta)
        if body_delta:
            for k, v in body_delta.items():
                if hasattr(new_state.body, k):
                    setattr(new_state.body, k, v)
        if self_delta:
            new_state.self_model.update(self_delta)
        if broadcast:
            new_state.workspace.extend(broadcast)
            # 限制工作空间大小
            if len(new_state.workspace) > 20:
                new_state.workspace = new_state.workspace[-20:]
        if evaluation:
            new_state.confidence.update(evaluation.get("confidence", {}))
            new_state.uncertainty.update(evaluation.get("uncertainty", {}))
            new_state.affect.update(evaluation.get("affect", {}))

        new_state.integrity_hash = new_state.compute_integrity_hash()
        return new_state
