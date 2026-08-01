"""EVA-MVSC 核心协议 — 所有模块必须遵循的接口契约。

这些协议定义了模块间的标准契约，使得:
1. 模块可独立替换
2. 消融实验可以精确关闭特定机制
3. 测试可以 mock 任意模块
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from packages.contracts.events import EventEnvelope
from packages.contracts.state import (
    BodyState,
    BroadcastContent,
    ConsciousState,
    ContentCandidate,
    Goal,
    Plan,
)


# ═══════════════════════════════════════════════════════════════
# RuntimeContext — 传递给每个模块的运行时上下文
# ═══════════════════════════════════════════════════════════════

class RuntimeContext:
    """传递给 CognitiveModule.handle() 的运行时上下文。

    包含当前状态引用和核心服务引用。
    模块通过此上下文访问系统资源，不直接访问全局变量。
    """

    def __init__(
        self,
        state: ConsciousState,
        event_bus: "EventBusProtocol | None" = None,
        state_repo: "StateRepository | None" = None,
        feature_flags: dict[str, bool] | None = None,
    ):
        self.state = state
        self.event_bus = event_bus
        self.state_repo = state_repo
        self.feature_flags = feature_flags or {}


# ═══════════════════════════════════════════════════════════════
# CognitiveModule — 所有认知模块的基础协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class CognitiveModule(Protocol):
    """所有认知模块必须实现的接口。

    每个模块:
    - 接收事件 + 运行时上下文
    - 返回零个或多个输出事件
    - 通过输出事件影响其他模块和状态

    模块不直接修改 ConsciousState — 它们通过发出事件来影响状态，
    状态的实际修改由 StateRepository.commit() 统一完成。
    """

    name: str

    async def handle(
        self,
        event: EventEnvelope,
        context: RuntimeContext,
    ) -> list[EventEnvelope]:
        """处理事件，返回输出事件列表。

        Args:
            event: 输入事件
            context: 运行时上下文（包含当前状态引用）

        Returns:
            输出事件列表（可能为空）
        """
        ...


# ═══════════════════════════════════════════════════════════════
# StateRepository — 状态持久化协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class StateRepository(Protocol):
    """状态仓库 — 唯一有权持久化权威状态的组件。"""

    async def load(self, subject_id: str) -> ConsciousState:
        """加载指定主体的最新权威状态。"""
        ...

    async def commit(
        self,
        previous_version: int,
        new_state: ConsciousState,
        events: list[EventEnvelope],
    ) -> int:
        """提交新状态（乐观锁）。

        Args:
            previous_version: 期望的当前版本号
            new_state: 新状态
            events: 导致此状态变更的事件列表

        Returns:
            新版本号

        Raises:
            VersionConflictError: 版本冲突（并发修改）
        """
        ...

    async def replay(
        self,
        subject_id: str,
        from_version: int = 0,
        to_version: int | None = None,
    ) -> list[EventEnvelope]:
        """重放事件，返回有序事件列表。

        用于恢复和审计。
        """
        ...


# ═══════════════════════════════════════════════════════════════
# EventBus — 事件总线协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class EventBusProtocol(Protocol):
    """事件总线协议。"""

    async def publish(self, event: EventEnvelope) -> bool:
        """发布事件。返回 True 表示成功入队。"""
        ...

    async def consume(self, timeout: float = 0.5) -> EventEnvelope | None:
        """消费事件。"""
        ...

    def subscribe(
        self,
        event_type: str,
        handler: "CognitiveModule",
    ) -> None:
        """订阅特定类型的事件。"""
        ...


# ═══════════════════════════════════════════════════════════════
# 模型层协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class WorldModelProtocol(Protocol):
    """世界模型协议。"""

    async def update(
        self,
        state: ConsciousState,
        event: EventEnvelope,
    ) -> dict:
        """根据事件更新世界模型，返回增量。"""
        ...

    async def query(self, query: str) -> list[dict]:
        """查询世界状态。"""
        ...


@runtime_checkable
class BodyModelProtocol(Protocol):
    """身体模型协议。"""

    async def update(
        self,
        state: ConsciousState,
        event: EventEnvelope,
    ) -> dict:
        """更新身体状态，返回增量。"""
        ...

    def check_viability(self, body: BodyState) -> list[str]:
        """检查可生存域，返回违规列表。"""
        ...


@runtime_checkable
class SelfModelProtocol(Protocol):
    """自我模型协议。"""

    async def attribute(
        self,
        state: ConsciousState,
        event: EventEnvelope,
        broadcast: list[BroadcastContent],
    ) -> dict:
        """自我归属 — 判断哪些内容属于自身行动的结果。"""
        ...

    async def update_capability(
        self,
        state: ConsciousState,
        action_result: dict,
    ) -> dict:
        """根据行动结果更新能力模型。"""
        ...


# ═══════════════════════════════════════════════════════════════
# 认知层协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class ContentEngineProtocol(Protocol):
    """内容生成引擎协议。"""

    async def generate(
        self,
        state: ConsciousState,
        event: EventEnvelope,
        world_delta: dict,
        body_delta: dict,
    ) -> list[ContentCandidate]:
        """生成候选内容。"""
        ...


@runtime_checkable
class AttentionProtocol(Protocol):
    """注意力机制协议。"""

    async def select(
        self,
        state: ConsciousState,
        candidates: list[ContentCandidate],
    ) -> list[ContentCandidate]:
        """从候选中选择获得注意的内容。

        返回按优先级排序的内容列表。
        每个候选保留各分量得分，确保可解释。
        """
        ...


@runtime_checkable
class WorkspaceProtocol(Protocol):
    """全局工作空间协议。"""

    async def broadcast(
        self,
        state: ConsciousState,
        selected: list[ContentCandidate],
    ) -> list[BroadcastContent]:
        """将选中的内容广播到工作空间。

        广播门控条件:
        - content.stability >= threshold
        - content.priority >= threshold
        - workspace.has_capacity()
        """
        ...


@runtime_checkable
class MetacognitionProtocol(Protocol):
    """元认知协议。"""

    async def evaluate(
        self,
        state: ConsciousState,
        broadcast: list[BroadcastContent],
        self_delta: dict,
    ) -> dict:
        """元认知评估。

        返回包含 confidence, uncertainty, affect 的字典。
        """
        ...


@runtime_checkable
class DecisionEngineProtocol(Protocol):
    """决策引擎协议。"""

    async def decide(
        self,
        state: ConsciousState,
        evaluation: dict,
    ) -> "Decision":
        """做出决策。"""
        ...


# ═══════════════════════════════════════════════════════════════
# AgentOS 协议
# ═══════════════════════════════════════════════════════════════

class Intent(BaseModel):
    """结构化意图。"""
    intent_id: str
    description: str
    goal_id: str | None = None
    priority: float = 0.5
    constraints: list[str] = []


class ToolRequest(BaseModel):
    """工具调用请求。"""
    tool_id: str
    parameters: dict
    token_id: str | None = None
    timeout_sec: float = 30.0


class ToolResult(BaseModel):
    """工具调用结果。"""
    ok: bool
    data: dict
    error: str | None = None
    duration_ms: int = 0
    side_effects: list[str] = []


class VerificationResult(BaseModel):
    """验证结果。"""
    passed: bool
    checks: list[dict]
    summary: str


class Decision(BaseModel):
    """决策结果。"""
    requires_action: bool
    intent: Intent | None = None
    reasoning: str = ""


@runtime_checkable
class IntentParserProtocol(Protocol):
    """意图解析器协议。"""

    async def parse(self, event: EventEnvelope, state: ConsciousState) -> Intent:
        """将输入事件解析为结构化意图。"""
        ...


@runtime_checkable
class PlannerProtocol(Protocol):
    """规划器协议。"""

    async def create(self, state: ConsciousState, decision: Decision) -> Plan:
        """根据决策创建执行计划。"""
        ...


@runtime_checkable
class ToolAdapterProtocol(Protocol):
    """工具适配器协议。"""

    tool_id: str

    async def validate(self, request: ToolRequest) -> None:
        """验证工具调用参数。"""
        ...

    async def execute(self, request: ToolRequest) -> ToolResult:
        """执行工具调用。"""
        ...


@runtime_checkable
class VerifierProtocol(Protocol):
    """验证器协议。"""

    async def verify(
        self,
        intent: Intent,
        plan: Plan,
        result: ToolResult,
    ) -> VerificationResult:
        """验证行动结果是否符合预期。"""
        ...


# ═══════════════════════════════════════════════════════════════
# 记忆协议
# ═══════════════════════════════════════════════════════════════

@runtime_checkable
class MemoryProtocol(Protocol):
    """记忆系统协议。"""

    async def consolidate(
        self,
        state: ConsciousState,
        source_event: EventEnvelope,
        broadcast: list[BroadcastContent],
        evaluation: dict,
        action_events: list[EventEnvelope],
    ) -> list[EventEnvelope]:
        """记忆整合 — 将当前循环的内容写入记忆。

        返回记忆相关事件列表。
        """
        ...

    async def retrieve(
        self,
        query: str,
        context: dict,
    ) -> list[dict]:
        """记忆检索。

        检索评分 = Similarity × Confidence × Authority
                   × Freshness × Permission × GoalRelevance
        """
        ...
