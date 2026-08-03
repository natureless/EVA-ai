"""Self-Model API — 自我模型可视化和管理端点。

端点:
- GET  /api/mvsc/self/identity   — 身份模型
- GET  /api/mvsc/self/capability  — 能力模型
- GET  /api/mvsc/self/boundary    — 边界模型
- GET  /api/mvsc/self/narrative   — 叙事模型
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.experimental import conscious_state

router = APIRouter(prefix="/api/mvsc/self", tags=["mvsc-self"])


@router.get("/identity")
def get_identity(request: Request) -> dict[str, Any]:
    """获取身份模型 — 版本化的稳定身份信息。"""
    cs = conscious_state(request.app.state.container.system_state)
    sm = cs.self_model
    identity = sm.get("identity", {})
    return {
        "system_id": identity.get("system_id", "eva-001"),
        "version": identity.get("identity_version", "?"),
        "role": identity.get("role_definition", ""),
        "principles": identity.get("core_principles", []),
        "change_history": identity.get("change_history", [])[-5:],
    }


@router.get("/capability")
def get_capability(request: Request) -> dict[str, Any]:
    """获取能力模型 — 系统知道自己能/不能做什么。"""
    cs = conscious_state(request.app.state.container.system_state)
    sm = cs.self_model
    capabilities = sm.get("capability", {}).get("capabilities", {})
    return {
        "capabilities": [
            {
                "id": cid,
                "name": c.get("name", cid),
                "confidence": c.get("confidence", 0.5),
                "success_count": c.get("success_count", 0),
                "failure_count": c.get("failure_count", 0),
            }
            for cid, c in capabilities.items()
        ],
        "total": len(capabilities),
    }


@router.get("/boundary")
def get_boundary(request: Request) -> dict[str, Any]:
    """获取边界模型 — 资源访问范围。"""
    cs = conscious_state(request.app.state.container.system_state)
    sm = cs.self_model
    boundary = sm.get("boundary", {})
    return {
        "tools": boundary.get("my_tools", []),
        "allowed_domains": boundary.get("allowed_domains", []),
        "forbidden_paths": boundary.get("forbidden_paths", [])[:10],
        "permission_scope": list(boundary.get("permission_scope", set())),
    }


@router.get("/narrative")
def get_narrative(request: Request, limit: int = 10) -> dict[str, Any]:
    """获取叙事模型 — 最近的关键事件节点。"""
    cs = conscious_state(request.app.state.container.system_state)
    sm = cs.self_model
    narrative = sm.get("narrative", {})
    nodes = narrative.get("nodes", [])[-limit:]
    return {
        "nodes": [
            {
                "id": n.get("node_id", ""),
                "summary": n.get("summary", "")[:200],
                "lessons": n.get("lessons", []),
                "evidence_count": len(n.get("evidence_event_ids", [])),
                "created_at": n.get("created_at", ""),
            }
            for n in nodes
        ],
        "total": len(narrative.get("nodes", [])),
        "current_chapter": narrative.get("current_chapter", ""),
    }


@router.get("/stability")
def get_stability(request: Request) -> dict[str, Any]:
    """获取自我稳定性指标。"""
    cs = conscious_state(request.app.state.container.system_state)
    sm = cs.self_model
    metrics = sm.get("stability_metrics", {})
    return {
        "stability_score": metrics.get("stability_score", 1.0),
        "mean_prediction_error": metrics.get("mean_prediction_error", 0.0),
        "total_perturbations": metrics.get("total_perturbations", 0),
        "recent_perturbation_count": metrics.get("recent_perturbation_count", 0),
    }
