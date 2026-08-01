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


@router.get("/observability")
def get_observability_snapshot(request: Request) -> dict[str, Any]:
    """获取可观测性快照：指标、追踪、健康。"""
    container = request.app.state.container

    result: dict[str, Any] = {"status": "mvsc_not_enabled"}

    if container.mvsc_components:
        result["status"] = "active"

        # Metrics
        collector = getattr(container, "ablation_collector", None)
        if collector:
            result["metrics"] = collector.to_dict()

        # Lifecycle
        ss = container.system_state
        result["lifecycle"] = {
            "mode": ss.get("mvsc_runtime_mode", "unknown"),
            "phase": ss.get("mvsc_cognition_phase", "unknown"),
            "tick": ss.get("mvsc_tick", 0),
        }

        # Feature flags
        result["features"] = {
            k: v for k, v in ss.get("mvsc_feature_flags", {}).items()
            if not v  # show only disabled features
        }

    return result


@router.get("/trace/{correlation_id}")
def get_trace(request: Request, correlation_id: str) -> dict[str, Any]:
    """获取事件关联ID的追踪信息。"""
    container = request.app.state.container

    if not container.mvsc_components:
        return {"status": "mvsc_not_enabled"}

    event_store = container.mvsc_components.get("event_store")
    if event_store is None:
        return {"status": "event_store_not_available"}

    try:
        events = event_store.get_by_correlation(correlation_id)
        return {
            "correlation_id": correlation_id,
            "event_count": len(events),
            "events": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "source": e.source,
                    "sequence": e.sequence,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in events[:50]
            ],
        }
    except Exception:
        return {"status": "error", "correlation_id": correlation_id}


@router.get("/integrity")
def verify_event_integrity(request: Request) -> dict[str, Any]:
    """验证 EventStore 事件日志完整性。

    检查:
    - 序列号连续性 (无gap)
    - 事件总数
    - 状态哈希确定性
    """
    container = request.app.state.container

    if not container.mvsc_components:
        return {"status": "mvsc_not_enabled"}

    event_store = container.mvsc_components.get("event_store")
    if event_store is None:
        return {"status": "event_store_not_available"}

    try:
        integrity = event_store.verify_integrity()
        state_hash = event_store.compute_state_hash()
        return {
            "status": "healthy" if integrity["ok"] else "degraded",
            "integrity": integrity,
            "state_hash": state_hash,
            "total_events": integrity["total"],
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/events")
def list_events(
    request: Request,
    subject_id: str = "eva-001",
    from_seq: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """列出 EventStore 中的事件（支持分页）。

    Args:
        subject_id: 主体ID
        from_seq: 起始序列号
        limit: 最大返回数 (默认 20, 最大 100)
    """
    container = request.app.state.container

    if not container.mvsc_components:
        return {"status": "mvsc_not_enabled", "events": []}

    event_store = container.mvsc_components.get("event_store")
    if event_store is None:
        return {"status": "event_store_not_available", "events": []}

    limit = min(limit, 100)
    try:
        events = event_store.replay(subject_id, from_sequence=from_seq)
        latest = event_store.get_latest_sequence(subject_id)
        return {
            "subject_id": subject_id,
            "from_sequence": from_seq,
            "latest_sequence": latest,
            "total": len(events),
            "events": [
                {
                    "sequence": e.sequence,
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "source": e.source,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "correlation_id": e.correlation_id,
                    "causation_id": e.causation_id,
                }
                for e in events[from_seq:from_seq + limit]
            ],
        }
    except Exception as e:
        return {"status": "error", "detail": str(e), "events": []}


@router.get("/cache/stats")
def get_semantic_cache_stats(request: Request) -> dict[str, Any]:
    """获取语义缓存统计信息。"""
    container = request.app.state.container

    cache = getattr(container, "mvsc_semantic_cache", None)
    if cache is None:
        return {"status": "cache_not_initialized"}

    return {
        "status": "active",
        "stats": cache.stats,
    }


@router.post("/cache/invalidate")
def invalidate_semantic_cache(request: Request) -> dict[str, Any]:
    """清空语义缓存。"""
    container = request.app.state.container

    cache = getattr(container, "mvsc_semantic_cache", None)
    if cache is None:
        return {"status": "cache_not_initialized"}

    count = cache.invalidate()
    return {"ok": True, "invalidated": count}
