"""Cognition observability API — 认知循环性能和阶段耗时趋势。

端点:
- GET /api/mvsc/cognition/timings — 最近阶段耗时
- GET /api/mvsc/cognition/stats  — 认知循环统计
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/mvsc/cognition", tags=["mvsc-cognition"])


@router.get("/timings")
def get_phase_timings(request: Request) -> dict[str, Any]:
    """获取最近一次认知循环的各阶段耗时。"""
    container = request.app.state.container

    if not container.mvsc_components:
        return {"status": "mvsc_not_enabled"}

    mvsc_loop = container.mvsc_components.get("mvsc_loop")
    if mvsc_loop is None:
        return {"status": "loop_not_available"}

    return {
        "phases": mvsc_loop.phase_timings,
        "tick": container.system_state.get("mvsc_tick", 0),
    }


@router.get("/stats")
def get_cognition_stats(request: Request) -> dict[str, Any]:
    """认知循环统计 — 速率、错误、延迟。"""
    container = request.app.state.container

    stats: dict[str, Any] = {
        "tick": container.system_state.get("mvsc_tick", 0),
        "focus": container.system_state.get("focus", "idle"),
        "mode": container.system_state.get("mvsc_runtime_mode", "unknown"),
    }

    # Legacy loop stats
    legacy_loop = container.loop
    if legacy_loop and hasattr(legacy_loop, "stats"):
        stats["legacy"] = legacy_loop.stats

    # MVSC loop stats
    if container.mvsc_components:
        mvsc_loop = container.mvsc_components.get("mvsc_loop")
        if mvsc_loop:
            stats["mvsc"] = {
                "features_enabled": sum(1 for v in mvsc_loop.feature_flags.values() if v),
                "features_total": len(mvsc_loop.feature_flags),
            }

    return stats
