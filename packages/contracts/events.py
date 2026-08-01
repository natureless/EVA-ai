"""EVA-MVSC 事件协议 — 统一事件信封和事件族定义。

这是整个系统中所有模块之间通信的唯一标准格式。
所有事件必须使用 EventEnvelope 封装，不允许裸 dict 传递。

事件族命名规范: {domain}.{action}_{detail}
例: perception.user_message_received, action.tool_completed
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# EventEnvelope — 统一事件信封
# ═══════════════════════════════════════════════════════════════

class EventEnvelope(BaseModel):
    """所有模块间通信的统一事件格式。

    每个事件必须包含完整的溯源信息：
    - event_id: 全局唯一标识
    - causation_id: 因果链（哪个事件导致了此事件）
    - correlation_id: 关联ID（同一会话/任务的事件共享）
    - subject_id: 主体ID（哪个EVA实例）
    - sequence: 单调递增序号（用于事件重放）
    """

    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    event_type: str  # 命名空间格式: "perception.user_message_received"
    source: str  # 产生事件的模块: "kernel", "cognition", "agentos", "user", ...
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ── 溯源 ──
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid4().hex[:8]}")
    causation_id: str | None = None  # 哪个事件直接导致此事件
    subject_id: str = "eva-001"  # override via EventEnvelope(subject_id=...)
    session_id: str | None = None
    sequence: int | None = None  # 由EventStore分配

    # ── 载荷 ──
    payload: dict[str, Any] = Field(default_factory=dict)

    # ── 元数据 ──
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    priority: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitivity: str = "internal"  # internal | user_visible | audit | sensitive
    schema_version: str = "1.0"

    model_config = {"frozen": False}  # 允许后续修改sequence等字段


# ═══════════════════════════════════════════════════════════════
# 事件族定义
# ═══════════════════════════════════════════════════════════════

class EventFamily:
    """事件类型命名空间常量。

    使用方式:
        EventFamily.PERCEPTION.USER_MESSAGE  → "perception.user_message_received"
        EventFamily.ACTION.TOOL_STARTED      → "action.tool_started"
    """

    class RUNTIME:
        STARTED = "runtime.started"
        STOPPING = "runtime.stopping"
        ERROR = "runtime.error"
        HEARTBEAT = "runtime.heartbeat"
        HEARTBEAT_CHALLENGE = "runtime.heartbeat_challenge"
        HEARTBEAT_RESPONSE = "runtime.heartbeat_response"
        MODE_CHANGED = "runtime.mode_changed"

    class PERCEPTION:
        USER_MESSAGE = "perception.user_message_received"
        SYSTEM_EVENT = "perception.system_event"
        GITHUB_EVENT = "perception.github_event"
        SCHEDULER_TICK = "perception.scheduler_tick"
        EXTERNAL_API = "perception.external_api_call"

    class WORLD:
        ENTITY_UPSERTED = "world.entity_upserted"
        ENTITY_REMOVED = "world.entity_removed"
        EDGE_LINKED = "world.edge_linked"
        BELIEF_UPDATED = "world.belief_updated"
        FOCUS_CHANGED = "world.focus_changed"

    class BODY:
        RESOURCE_UPDATED = "body.resource_updated"
        RESOURCE_RISK = "body.resource_risk_detected"
        VIABILITY_BREACHED = "body.viability_breached"
        HEALTH_CHECK = "body.health_check"

    class ATTENTION:
        CONTENT_CANDIDATE = "attention.content_candidate"
        CONTENT_SELECTED = "attention.content_selected"
        CONTENT_INHIBITED = "attention.content_inhibited"

    class WORKSPACE:
        CONTENT_BROADCAST = "workspace.content_broadcast"
        CONTENT_RETRACTED = "workspace.content_retracted"
        CAPACITY_THRESHOLD = "workspace.capacity_threshold"

    class SELF:
        ACTION_ATTRIBUTED = "self.action_attributed"
        CAPABILITY_UPDATED = "self.capability_updated"
        BOUNDARY_CHECKED = "self.boundary_checked"
        PREDICTION_ERROR = "self.prediction_error"
        PERTURBATION_RECORDED = "self.perturbation_recorded"
        IDENTITY_CHANGE_PROPOSED = "self.identity_change_proposed"

    class GOAL:
        CREATED = "goal.created"
        UPDATED = "goal.updated"
        COMPLETED = "goal.completed"
        FAILED = "goal.failed"
        ABANDONED = "goal.abandoned"

    class PLAN:
        CREATED = "plan.created"
        STEP_STARTED = "plan.step_started"
        STEP_COMPLETED = "plan.step_completed"
        STEP_FAILED = "plan.step_failed"
        RETRY_SCHEDULED = "plan.retry_scheduled"

    class ACTION:
        TOOL_STARTED = "action.tool_started"
        TOOL_COMPLETED = "action.tool_completed"
        TOOL_FAILED = "action.tool_failed"
        AGENT_INVOKED = "action.agent_invoked"
        AGENT_COMPLETED = "action.agent_completed"
        EXECUTOR_GATED = "action.executor_gated"

    class VERIFICATION:
        PASSED = "verification.passed"
        FAILED = "verification.failed"
        NEEDS_HUMAN = "verification.needs_human"

    class MEMORY:
        EPISODE_COMMITTED = "memory.episode_committed"
        EPISODE_CORRECTED = "memory.episode_corrected"
        EPISODE_SUPERSEDED = "memory.episode_superseded"
        EPISODE_EXPIRED = "memory.episode_expired"
        CONSOLIDATED = "memory.consolidated"
        RETRIEVED = "memory.retrieved"

    class IDENTITY:
        CHANGE_PROPOSED = "identity.change_proposed"
        CHANGE_APPROVED = "identity.change_approved"
        CHANGE_REJECTED = "identity.change_rejected"
        VERSION_CREATED = "identity.version_created"

    class LIFECYCLE:
        CHECKPOINT_CREATED = "lifecycle.checkpoint_created"
        SNAPSHOT_SAVED = "lifecycle.snapshot_saved"
        RECOVERY_STARTED = "lifecycle.recovery_started"
        RECOVERY_COMPLETED = "lifecycle.recovery_completed"
        MAINTENANCE_STARTED = "lifecycle.maintenance_started"
        MAINTENANCE_COMPLETED = "lifecycle.maintenance_completed"

    class SECURITY:
        POLICY_VIOLATION = "security.policy_violation"
        UNAUTHORIZED_ACCESS = "security.unauthorized_access"
        TOKEN_EXPIRED = "security.token_expired"
        QUARANTINE_ENTERED = "security.quarantine_entered"

    class EXPERIMENT:
        FEATURE_TOGGLED = "experiment.feature_toggled"
        ABLATION_STARTED = "experiment.ablation_started"
        ABLATION_ENDED = "experiment.ablation_ended"
        METRIC_RECORDED = "experiment.metric_recorded"


# ═══════════════════════════════════════════════════════════════
# 向后兼容转换
# ═══════════════════════════════════════════════════════════════

# 旧事件类型 → 新事件类型映射
LEGACY_EVENT_MAP: dict[str, str] = {
    "user_message": EventFamily.PERCEPTION.USER_MESSAGE,
    "maintenance": EventFamily.LIFECYCLE.MAINTENANCE_STARTED,
    "reminder_trigger": EventFamily.PERCEPTION.SYSTEM_EVENT,
    "system_tick": EventFamily.PERCEPTION.SCHEDULER_TICK,
}


def from_legacy_event(legacy_event: Any, subject_id: str = "eva-001") -> EventEnvelope:
    """将旧的 Event 对象转换为新的 EventEnvelope。

    用于逐步迁移：现有代码可以继续使用旧Event，
    在EventBus入口处自动转换。
    """
    new_type = LEGACY_EVENT_MAP.get(legacy_event.type, f"legacy.{legacy_event.type}")
    return EventEnvelope(
        event_id=legacy_event.id,
        event_type=new_type,
        source=legacy_event.source,
        timestamp=legacy_event.timestamp if hasattr(legacy_event, 'timestamp') else datetime.now(timezone.utc),
        correlation_id=legacy_event.correlation_id or f"corr_{uuid4().hex[:8]}",
        subject_id=subject_id,
        payload=legacy_event.payload,
    )
