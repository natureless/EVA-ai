"""EVA-MVSC 自我模型 — 六子模型完整定义。

SelfModel 不是单一JSON文件，而是六个相互关联但可独立操作的结构:

1. IdentityModel   — 稳定身份，版本化，不可直接覆写
2. DigitalBodyModel — 数字身体状态（指向 BodyState）
3. BoundaryModel    — 资源边界："什么属于我"
4. AgencyModel      — 行动归属：ActionReceipt 链
5. CapabilityModel  — 能力自知："我能/不能做什么"
6. NarrativeModel   — 叙事自我：跨时间筛选的事件结构

关键原则:
- 身份只能通过 IdentityChangeProposal → 审批 → 新版本的路径演化
- 叙事不能脱离底层事件证据独立改写
- 能力模型必须在失败后降低置信度
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# 1. IdentityModel — 稳定身份
# ═══════════════════════════════════════════════════════════════

class IdentityModel(BaseModel):
    """稳定身份模型 — 版本化，不可运行时直接覆写。

    任何人格演化必须经过:
    观察长期模式 → IdentityChangeProposal → 风险评估 → 用户批准
    → 生成新版本 → 保留旧版本和变更理由
    """

    system_id: str = "eva-001"
    identity_version: str = "1.0.0"
    owner_id: str = "default_user"

    core_principles: list[str] = Field(default_factory=lambda: [
        "do not fabricate memories",
        "do not overclaim certainty",
        "do not violate user boundaries",
        "maintain audit trail for all actions",
    ])

    role_definition: str = "persistent cognitive assistant with stable identity"
    creation_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # 哪些字段允许通过什么方式修改
    authorized_mutability: dict[str, list[str]] = Field(default_factory=lambda: {
        "role_definition": ["identity_change_proposal"],
        "core_principles": ["identity_change_proposal"],
        "owner_id": [],  # 不可修改
        "system_id": [],  # 不可修改
    })

    # 变更历史
    change_history: list[dict[str, Any]] = Field(default_factory=list)


class IdentityChangeProposal(BaseModel):
    """身份变更提案 — 不可直接覆写身份，必须经过此流程。"""

    proposal_id: str = Field(default_factory=lambda: f"icp_{uuid4().hex[:8]}")
    proposed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    target_field: str
    current_value: Any
    proposed_value: Any
    reason: str
    evidence_event_ids: list[str] = Field(default_factory=list)  # 支撑证据
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"  # pending | approved | rejected
    approved_by: str | None = None
    approved_at: datetime | None = None


# ═══════════════════════════════════════════════════════════════
# 2. DigitalBodyModel — 数字身体
# ═══════════════════════════════════════════════════════════════

class DigitalBodyModel(BaseModel):
    """数字身体模型 — 指向 BodyState 并提供身体感知。

    数字身体不是物理身体，而是进程、存储、网络、预算的集合。
    价值系统的根基来自可生存域的边界检查。
    """

    body_id: str = "body-001"
    last_health_check: datetime | None = None
    health_check_interval_sec: float = 30.0

    # 历史资源状态（用于趋势分析）
    resource_history: list[dict[str, Any]] = Field(default_factory=list)

    # 自证心跳状态
    last_challenge: str | None = None
    last_challenge_at: datetime | None = None
    challenge_response_time_ms: float = 0.0

    def record_resource_snapshot(self, body_state: Any) -> None:
        """记录资源快照到历史。"""
        snapshot = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cpu": getattr(body_state, 'cpu_load', 0),
            "memory": getattr(body_state, 'memory_usage', 0),
            "disk": getattr(body_state, 'disk_usage', 0),
            "error_rate": getattr(body_state, 'error_rate', 0),
        }
        self.resource_history.append(snapshot)
        if len(self.resource_history) > 100:
            self.resource_history = self.resource_history[-100:]


# ═══════════════════════════════════════════════════════════════
# 3. BoundaryModel — 资源边界
# ═══════════════════════════════════════════════════════════════

class BoundaryModel(BaseModel):
    """边界模型 — 回答"什么属于我"。

    核心问题:
    - 哪些资源属于我？
    - 哪些进程属于我？
    - 哪些行动由我发起？
    - 哪些文件允许我访问？
    - 哪些工具是我的能力？
    - 哪些事件来自外部？
    """

    # 资源边界
    my_processes: list[str] = Field(default_factory=list)
    my_files: list[str] = Field(default_factory=list)
    my_tools: list[str] = Field(default_factory=list)

    # 网络边界
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])

    # 文件系统边界
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=lambda: [
        "/etc", "/root", "/boot", "/sys", "/proc",
    ])

    # 权限范围
    permission_scope: set[str] = Field(default_factory=set)

    def is_within_boundary(self, resource: str, resource_type: str) -> bool:
        """检查资源是否在边界内。"""
        if resource_type == "path":
            forbidden = any(resource.startswith(p) for p in self.forbidden_paths)
            if forbidden:
                return False
            return any(resource.startswith(p) for p in self.allowed_paths)
        if resource_type == "domain":
            import fnmatch
            return any(fnmatch.fnmatch(resource, d) for d in self.allowed_domains)
        if resource_type == "tool":
            return resource in self.my_tools
        return False


# ═══════════════════════════════════════════════════════════════
# 4. AgencyModel — 行动归属
# ═══════════════════════════════════════════════════════════════

class ActionReceipt(BaseModel):
    """行动收据 — 每次行动必须产生。

    行动归属依据:
    1. 是否由当前意图产生
    2. 是否经过自身规划
    3. 是否由自身权限令牌发出
    4. 预期结果与实际结果是否匹配
    5. 时间顺序是否一致
    6. 是否存在外部更合理原因

    不能因为"结果符合预测"就判定为自身行动。
    """

    action_id: str = Field(default_factory=lambda: f"act_{uuid4().hex[:8]}")
    intent_id: str
    plan_id: str | None = None
    actor_id: str  # 谁执行了行动: "self" | tool_id | "external"
    tool_id: str | None = None
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    pre_state_hash: str = ""
    post_state_hash: str | None = None

    expected_effects: list[str] = Field(default_factory=list)
    observed_effects: list[str] = Field(default_factory=list)

    status: str = "pending"  # pending | executing | completed | failed | external
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # 归属证据
    attribution_evidence: dict[str, Any] = Field(default_factory=dict)


class AgencyModel(BaseModel):
    """行动归属模型 — 追踪行动的所有权和因果链。"""

    action_history: list[ActionReceipt] = Field(default_factory=list)
    self_initiated_count: int = 0
    external_count: int = 0
    attribution_errors: int = 0

    def attribute(self, receipt: ActionReceipt, context: dict) -> str:
        """判断行动归属。

        Returns:
            "self" | "tool:{tool_id}" | "external"
        """
        # 检查是否由自身意图驱动
        if receipt.intent_id and context.get("has_active_intent"):
            evidence = {
                "has_intent": True,
                "has_plan": receipt.plan_id is not None,
                "has_token": context.get("token_id") is not None,
                "time_consistent": True,
            }
            receipt.attribution_evidence = evidence
            return "self"

        # 检查是否是工具执行
        if receipt.tool_id:
            return f"tool:{receipt.tool_id}"

        return "external"

    def record_action(self, receipt: ActionReceipt) -> None:
        """记录行动收据。"""
        self.action_history.append(receipt)
        if len(self.action_history) > 500:
            self.action_history = self.action_history[-500:]

        actor = receipt.actor_id
        if actor == "self":
            self.self_initiated_count += 1
        elif actor.startswith("tool:"):
            pass
        else:
            self.external_count += 1


# ═══════════════════════════════════════════════════════════════
# 5. CapabilityModel — 能力自知
# ═══════════════════════════════════════════════════════════════

class CapabilityEntry(BaseModel):
    """单个能力的自知条目。"""

    capability_id: str
    name: str
    description: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    success_count: int = 0
    failure_count: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_reason: str | None = None
    required_tools: list[str] = Field(default_factory=list)
    required_resources: dict[str, Any] = Field(default_factory=dict)


class CapabilityModel(BaseModel):
    """能力自知模型 — 系统知道自己能/不能做什么。

    这使"我不知道"成为可计算状态，而不是语言风格。
    """

    capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)

    # 全局不确定性
    known_unknowns: list[str] = Field(default_factory=list)  # 知道不知道什么
    unknown_unknowns_estimate: float = Field(default=0.3)  # 估计不知道什么

    def can_do(self, capability_id: str, min_confidence: float = 0.5) -> bool:
        """检查是否有足够信心执行某个能力。"""
        entry = self.capabilities.get(capability_id)
        if entry is None:
            return False
        return entry.confidence >= min_confidence

    def record_success(self, capability_id: str) -> None:
        """记录能力执行成功。"""
        if capability_id not in self.capabilities:
            return
        entry = self.capabilities[capability_id]
        entry.success_count += 1
        entry.last_success_at = datetime.now(timezone.utc)
        entry.last_attempt_at = datetime.now(timezone.utc)
        # 成功后提升置信度（向1.0靠近）
        entry.confidence = min(1.0, entry.confidence + 0.05)

    def record_failure(self, capability_id: str, reason: str) -> None:
        """记录能力执行失败 — 必须降低置信度。"""
        if capability_id not in self.capabilities:
            return
        entry = self.capabilities[capability_id]
        entry.failure_count += 1
        entry.last_failure_reason = reason
        entry.last_attempt_at = datetime.now(timezone.utc)
        # 失败后降低置信度（向0.0靠近）
        entry.confidence = max(0.1, entry.confidence - 0.1)

    def register_capability(
        self,
        capability_id: str,
        name: str,
        description: str,
        required_tools: list[str] | None = None,
    ) -> CapabilityEntry:
        """注册新能力。"""
        entry = CapabilityEntry(
            capability_id=capability_id,
            name=name,
            description=description,
            required_tools=required_tools or [],
        )
        self.capabilities[capability_id] = entry
        return entry


# ═══════════════════════════════════════════════════════════════
# 6. NarrativeModel — 叙事自我
# ═══════════════════════════════════════════════════════════════

class NarrativeNode(BaseModel):
    """叙事节点 — 经过筛选的跨时间结构。

    叙事不是全部事件的集合，而是经过重要性筛选、
    整合后的有意义结构。每个叙事节点必须关联底层事件证据。
    """

    node_id: str = Field(default_factory=lambda: f"narr_{uuid4().hex[:8]}")
    time_range: tuple[datetime, datetime] | None = None
    summary: str
    goals_involved: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    identity_implications: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)  # 必须关联证据！
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    supersedes: list[str] = Field(default_factory=list)  # 替代的旧叙事节点


class NarrativeModel(BaseModel):
    """叙事自我模型 — 跨时间筛选和整合的事件结构。

    关键约束:
    - 叙事不得脱离底层事件证据独立改写
    - 每个 NarrativeNode 必须关联 evidence_event_ids
    - 叙事修正通过 supersedes 链，不直接覆写
    """

    nodes: list[NarrativeNode] = Field(default_factory=list)
    current_chapter: str = ""  # 当前叙事章节摘要
    last_consolidated_at: datetime | None = None

    def add_node(self, node: NarrativeNode) -> None:
        """添加叙事节点。"""
        # 验证证据关联
        if not node.evidence_event_ids:
            raise ValueError("NarrativeNode must have evidence_event_ids")
        self.nodes.append(node)
        if len(self.nodes) > 200:
            self.nodes = self.nodes[-200:]

    def get_chapter_context(self, max_nodes: int = 5) -> str:
        """获取当前叙事上下文（用于注入系统提示）。"""
        recent = sorted(
            self.nodes, key=lambda n: n.created_at, reverse=True
        )[:max_nodes]
        parts = []
        for node in recent:
            parts.append(f"[{node.node_id}] {node.summary}")
        return "\n".join(parts) if parts else ""


# ═══════════════════════════════════════════════════════════════
# 统一 SelfModel
# ═══════════════════════════════════════════════════════════════

class SelfModel(BaseModel):
    """统一自我模型 — 组合六个子模型。

    这是 ConsciousState.self_model 字段的完整内容。
    """

    identity: IdentityModel = Field(default_factory=IdentityModel)
    body: DigitalBodyModel = Field(default_factory=DigitalBodyModel)
    boundary: BoundaryModel = Field(default_factory=BoundaryModel)
    agency: AgencyModel = Field(default_factory=AgencyModel)
    capability: CapabilityModel = Field(default_factory=CapabilityModel)
    narrative: NarrativeModel = Field(default_factory=NarrativeModel)

    # 兼容旧版 self_model JSON
    stability_metrics: dict[str, Any] = Field(default_factory=lambda: {
        "stability_score": 1.0,
        "mean_prediction_error": 0.0,
        "total_perturbations": 0,
    })

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（兼容旧格式）。"""
        return self.model_dump(mode="json")

    @classmethod
    def from_legacy(cls, legacy_data: dict) -> "SelfModel":
        """从旧的 self_model.json 创建（向后兼容）。"""
        model = cls()
        if "system_id" in legacy_data:
            model.identity = IdentityModel(**legacy_data)
        if "stability_metrics" in legacy_data:
            model.stability_metrics = legacy_data["stability_metrics"]
        return model
