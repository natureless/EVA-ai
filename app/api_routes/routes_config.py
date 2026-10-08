"""System Config API — 运行时配置查看。

端点:
- GET /api/config — 查看当前系统配置（脱敏）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from app.runtime_observation import runtime_observation

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config(request: Request) -> dict[str, Any]:
    """查看当前系统配置（敏感信息已脱敏）。"""
    container = request.app.state.container
    settings = container.settings
    observation = runtime_observation(container)

    return {
        "app": {
            "name": settings.app_name,
            "env": settings.env,
            "port": settings.port,
            "log_level": settings.log_level,
        },
        "storage": {
            "backend": settings.storage_backend,
            "db_path": str(settings.db_path),
        },
        "timing": {
            "tick_interval_sec": settings.tick_interval_sec,
            "queue_poll_timeout_sec": settings.queue_poll_timeout_sec,
            "scheduler_tick_interval_sec": settings.scheduler_tick_interval_sec,
            "stagnation_threshold_sec": settings.stagnation_threshold_sec,
        },
        "mvsc": {
            # Compatibility: enabled is a configuration flag, not an active consumer.
            "enabled": settings.enable_mvsc_pipeline,
            **observation["extensions"]["mvsc"],
            "subject_id": settings.mvsc_subject_id,
            "worker_count": settings.cognition_worker_count,
        },
        "business_goals": observation["extensions"]["business_goals"],
        "llm": {
            "timeout_sec": settings.llm_timeout_sec,
            "max_retries": settings.llm_max_retries,
            "embedding_provider": settings.embedding_provider,
        },
        "runtime": {
            "mode": observation["mode"],
            "phase": observation["phase"],
            "consumer_type": observation.get("consumer_type"),
            "requested": observation["requested"],
            "bootstrap_sec": container.system_state.get("bootstrap_sec", 0),
            "uptime_events": container.event_bus.size(),
            "agents_registered": len(container.registry.list_agents()),
            "agent_worker": container.runtime.worker_backend.stats,
        },
    }


@router.get("/defaults")
def config_defaults(request: Request) -> dict[str, Any]:
    """配置默认值 — 用于对比当前配置。"""
    from app.config import Settings

    # Settings() reads the process environment, so it is not the default config.
    defaults = {name: field.default for name, field in Settings.model_fields.items()}

    return {
        "defaults": {
            "env": defaults["env"],
            "port": defaults["port"],
            "log_level": defaults["log_level"],
            "tick_interval_sec": defaults["tick_interval_sec"],
            "stagnation_threshold_sec": defaults["stagnation_threshold_sec"],
            "enable_mvsc_pipeline": defaults["enable_mvsc_pipeline"],
            "enable_minimal_brain": defaults["enable_minimal_brain"],
            "enable_business_goals": defaults["enable_business_goals"],
            "enable_processing_episodes": defaults["enable_processing_episodes"],
            "enable_durable_requests": defaults["enable_durable_requests"],
            "resume_durable_requests": defaults["resume_durable_requests"],
            "enable_action_context": defaults["enable_action_context"],
            "cognition_worker_count": defaults["cognition_worker_count"],
            "agent_worker_count": defaults["agent_worker_count"],
            "agent_worker_backend": defaults["agent_worker_backend"],
            "agent_worker_queue_capacity": defaults["agent_worker_queue_capacity"],
            "agent_worker_queue_timeout_sec": defaults[
                "agent_worker_queue_timeout_sec"
            ],
            "agent_worker_max_tasks": defaults["agent_worker_max_tasks"],
            "agent_execution_timeout_sec": defaults["agent_execution_timeout_sec"],
            "llm_timeout_sec": defaults["llm_timeout_sec"],
            "embedding_provider": defaults["embedding_provider"],
        },
    }
