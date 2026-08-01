"""MVSC API routes — 暴露 MVSC 状态、消融配置和指标。

端点:
- GET  /api/mvsc/state       — MVSC ConsciousState 视图
- GET  /api/mvsc/ablation     — 当前消融配置
- POST /api/mvsc/ablation/toggle — 运行时切换特性
- GET  /api/mvsc/metrics      — 消融指标快照
- GET  /api/mvsc/lifecycle    — 生命周期状态
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.kernel.state_bridge import system_state_to_conscious
from packages.mvsc_lab.integration import toggle_feature

router = APIRouter(prefix="/api/mvsc", tags=["mvsc"])
logger = logging.getLogger("eva.api.mvsc")


# ═══════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════

class ToggleRequest(BaseModel):
    feature: str = Field(min_length=1, max_length=64)
    enabled: bool


# ═══════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════

@router.get("/state")
def get_mvsc_state(request: Request) -> dict[str, Any]:
    """获取 MVSC ConsciousState 视图。

    将现有 system_state 提升为 ConsciousState 格式返回。
    当 MVSC pipeline 未启用时仍可工作（使用默认值）。
    """
    container = request.app.state.container
    cs = system_state_to_conscious(container.system_state)
    return {
        "subject_id": cs.subject_id,
        "tick": cs.tick,
        "version": cs.version,
        "runtime_mode": cs.runtime_mode.value,
        "cognition_phase": cs.cognition_phase.value,
        "world": cs.world,
        "body": {
            "error_rate": cs.body.error_rate,
            "consecutive_failures": cs.body.consecutive_failures,
        },
        "workspace_size": len(cs.workspace),
        "active_contents": len(cs.active_contents),
        "goals_count": len(cs.goals),
        "confidence": cs.confidence,
        "uncertainty": cs.uncertainty,
        "integrity_hash": cs.integrity_hash,
    }


@router.get("/ablation")
def get_ablation_config(request: Request) -> dict[str, Any]:
    """获取当前消融配置。"""
    container = request.app.state.container

    flags = container.system_state.get("mvsc_feature_flags", {})
    if not flags:
        return {"status": "mvsc_not_enabled", "features": {}}

    enabled = [k for k, v in flags.items() if v]
    disabled = [k for k, v in flags.items() if not v]

    return {
        "status": "active",
        "features": flags,
        "enabled_count": len(enabled),
        "disabled_count": len(disabled),
        "enabled": enabled,
        "disabled": disabled,
    }


@router.post("/ablation/toggle")
def toggle_ablation_feature(req: ToggleRequest, request: Request) -> Any:
    """运行时切换消融特性。

    用于 A/B 测试和消融实验。
    """
    container = request.app.state.container

    if not hasattr(container, "ablation_config") or container.ablation_config is None:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "MVSC pipeline not enabled — cannot toggle features"},
        )

    success = toggle_feature(container, req.feature, req.enabled)
    if not success:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"Unknown feature: {req.feature}"},
        )

    return {
        "ok": True,
        "feature": req.feature,
        "enabled": req.enabled,
        "current_config": container.system_state.get("mvsc_feature_flags", {}),
    }


@router.get("/metrics")
def get_ablation_metrics(request: Request) -> dict[str, Any]:
    """获取消融指标快照。"""
    container = request.app.state.container

    collector = getattr(container, "ablation_collector", None)
    if collector is None:
        return {"status": "mvsc_not_enabled", "metrics": {}}

    return {
        "status": "active",
        "metrics": collector.to_dict(),
    }


@router.get("/lifecycle")
def get_lifecycle_state(request: Request) -> dict[str, Any]:
    """获取生命周期状态。"""
    container = request.app.state.container
    ss = container.system_state

    return {
        "runtime_mode": ss.get("mvsc_runtime_mode", "unknown"),
        "cognition_phase": ss.get("mvsc_cognition_phase", "unknown"),
        "mvsc_tick": ss.get("mvsc_tick", 0),
        "mvsc_version": ss.get("mvsc_version", 0),
        "mvsc_enabled": bool(container.mvsc_components),
        "feature_flags": ss.get("mvsc_feature_flags", {}),
    }


@router.get("/health/full")
def get_full_health(request: Request) -> dict[str, Any]:
    """聚合健康检查：现有 health + MVSC 状态。"""
    container = request.app.state.container
    ss = container.system_state

    mvsc_health = "not_enabled"
    if container.mvsc_components:
        mvsc_loop = container.mvsc_components.get("mvsc_loop")
        if mvsc_loop:
            mvsc_health = "active" if ss.get("loop_ready") else "degraded"

    return {
        "status": "healthy" if ss.get("ready") else "degraded",
        "mvsc": {
            "enabled": bool(container.mvsc_components),
            "status": mvsc_health,
            "tick": ss.get("mvsc_tick", 0),
            "mode": ss.get("mvsc_runtime_mode", "unknown"),
        },
        "components": {
            "db": ss.get("db_ready", False),
            "event_bus": ss.get("event_bus_ready", False),
            "loop": ss.get("loop_ready", False),
            "scheduler": ss.get("scheduler_running", False),
        },
    }


@router.get("/verification/stats")
def get_verification_stats(request: Request) -> dict[str, Any]:
    """获取 Verifier 统计信息。"""
    container = request.app.state.container

    if not container.mvsc_components:
        return {"status": "mvsc_not_enabled"}

    mvsc_loop = container.mvsc_components.get("mvsc_loop")
    if mvsc_loop and mvsc_loop._verifier:
        return {
            "status": "active",
            "stats": mvsc_loop._verifier.stats,
        }

    return {"status": "verifier_not_initialized"}
