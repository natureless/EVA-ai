"""Versioned event metadata shared by the stable runtime and MVSC experiments.

The envelope records provenance; it does not prove truth, durable admission,
causality or exactly-once execution. Experimental event types remain extensible.
"""
from datetime import datetime, timezone
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, revalidate_instances="always")

    schema_version: Literal["1.0"] = "1.0"
    source: str = Field(min_length=1, max_length=512)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    causation_id: str | None = None
    source_event_id: str | None = Field(default=None, min_length=1, max_length=1024)
    subject_id: str = "eva-001"
    session_id: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    priority: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    sensitivity: str = "internal"
    status: str = "pending"
    compatibility: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload", "compatibility")
    @classmethod
    def json_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("event data must be finite JSON values") from exc
        return value

    @field_validator("timestamp")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp requires an explicit UTC offset")
        return value.astimezone(timezone.utc)


class EventEnvelope(EventMetadata):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}", min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=128)


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
        GITHUB_PUSH = "perception.github_push"
        GITHUB_PR = "perception.github_pr"
        GITHUB_ISSUE = "perception.github_issue"
        GITHUB_WORKFLOW = "perception.github_workflow"
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
