"""System Config API — 运行时配置查看。

端点:
- GET /api/config — 查看当前系统配置（脱敏）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config(request: Request) -> dict[str, Any]:
    """查看当前系统配置（敏感信息已脱敏）。"""
    container = request.app.state.container
    settings = container.settings

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
            "enabled": settings.enable_mvsc_pipeline,
            "subject_id": settings.mvsc_subject_id,
            "worker_count": settings.cognition_worker_count,
        },
        "llm": {
            "timeout_sec": settings.llm_timeout_sec,
            "max_retries": settings.llm_max_retries,
            "embedding_provider": settings.embedding_provider,
        },
        "runtime": {
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
    defaults = Settings()

    return {
        "defaults": {
            "env": defaults.env,
            "port": defaults.port,
            "log_level": defaults.log_level,
            "tick_interval_sec": defaults.tick_interval_sec,
            "stagnation_threshold_sec": defaults.stagnation_threshold_sec,
            "enable_mvsc_pipeline": defaults.enable_mvsc_pipeline,
            "cognition_worker_count": defaults.cognition_worker_count,
            "agent_worker_count": defaults.agent_worker_count,
            "agent_execution_timeout_sec": defaults.agent_execution_timeout_sec,
            "llm_timeout_sec": defaults.llm_timeout_sec,
            "embedding_provider": defaults.embedding_provider,
        },
    }
