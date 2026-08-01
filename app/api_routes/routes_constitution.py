"""Constitution API — 宪法文件管理和热加载。

端点:
- GET  /api/constitution         — 查看当前宪法
- POST /api/constitution/reload  — 热加载宪法文件
- GET  /api/constitution/status  — 宪法加载状态
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/constitution", tags=["constitution"])
logger = logging.getLogger("eva.api.constitution")

CONSTITUTION_PATH = Path("constitution.yaml")


@router.get("")
def get_constitution(request: Request) -> dict[str, Any]:
    """返回当前加载的宪法内容（敏感字段已脱敏）。"""
    container = request.app.state.container
    const = _load_file()

    # 脱敏：移除内部配置细节
    safe = {
        "version": const.get("version", "?"),
        "name": const.get("name", "?"),
        "state_machine": const.get("state_machine", {}).get("states", {}),
        "priority_system": _summarize_priorities(const),
        "boundaries": _summarize_boundaries(const),
        "high_risk_operations": const.get("high_risk_operations", {}).get("requires_confirmation", [])[:10],
        "loaded_at": getattr(container, "_constitution_loaded_at", None),
    }
    return safe


@router.post("/reload")
def reload_constitution(request: Request) -> dict[str, Any]:
    """热加载 constitution.yaml 并更新 PolicyEngine。

    不需要重启服务。加载失败时保持当前宪法不变。
    """
    container = request.app.state.container

    if not CONSTITUTION_PATH.exists():
        raise HTTPException(status_code=404, detail="constitution.yaml not found")

    try:
        new_const = _load_file()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse: {e}")

    # 更新 PolicyEngine
    if hasattr(container, "policy_engine") and container.policy_engine:
        try:
            from core.policy_engine import PolicyEngine
            # Rebuild policy config from new constitution
            policy_config = _build_policy_config(new_const)
            container.policy_engine = PolicyEngine(policy_config)
            container._constitution_loaded_at = datetime.now(timezone.utc).isoformat()
            container.system_state["policy_state"] = container.policy_engine.get_state()
            logger.info("constitution reloaded successfully (version=%s)", new_const.get("version", "?"))
        except Exception as e:
            logger.exception("failed to apply reloaded constitution to PolicyEngine")
            raise HTTPException(status_code=500, detail=f"Failed to apply: {e}")

    return {
        "ok": True,
        "version": new_const.get("version", "?"),
        "reloaded_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status")
def constitution_status(request: Request) -> dict[str, Any]:
    """宪法加载状态。"""
    container = request.app.state.container

    exists = CONSTITUTION_PATH.exists()
    loaded_at = getattr(container, "_constitution_loaded_at", None)
    policy_state = container.system_state.get("policy_state", {})

    return {
        "file_exists": exists,
        "file_path": str(CONSTITUTION_PATH.absolute()),
        "loaded_at": loaded_at,
        "policy_state_machine": policy_state.get("state_machine", {}).get("current", "unknown"),
        "active_tokens": policy_state.get("tokens", {}).get("active_tokens", 0),
    }


def _load_file() -> dict[str, Any]:
    """加载并解析 constitution.yaml。"""
    with CONSTITUTION_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not data:
        raise ValueError("constitution.yaml is empty")
    return data


def _build_policy_config(const: dict[str, Any]) -> dict[str, Any]:
    """从宪法构建 PolicyEngine 配置。"""
    config: dict[str, Any] = {}
    if const.get("state_machine"):
        config["state_machine"] = const["state_machine"]
    if const.get("priority_system"):
        config["priority_system"] = const["priority_system"]
    return config


def _summarize_priorities(const: dict[str, Any]) -> list[str]:
    ps = const.get("priority_system", {})
    return [f"{k}: {v.get('name', '?')}" for k, v in ps.items() if k not in ("description",) and isinstance(v, dict)]


def _summarize_boundaries(const: dict[str, Any]) -> dict[str, Any]:
    b = const.get("boundaries", {})
    return {
        "network_mode": b.get("network", {}).get("mode", "?"),
        "allowed_domains": b.get("network", {}).get("allowed_domains", [])[:5],
        "max_file_size_mb": b.get("filesystem", {}).get("max_file_size_mb", "?"),
    }
