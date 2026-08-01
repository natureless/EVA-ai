"""System metadata API — 迁移状态、API总览、日志级别。

端点:
- GET /api/system/migrations — 数据库迁移状态
- GET /api/system/endpoints — API 端点总览
- POST /api/system/log-level — 动态调整日志级别
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/system", tags=["system"])
logger = logging.getLogger("eva.api.system")


class LogLevelRequest(BaseModel):
    level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


@router.get("/migrations")
def migration_status(request: Request) -> dict[str, Any]:
    """数据库迁移状态。"""
    container = request.app.state.container
    store = container.store

    if store is None:
        return {"status": "unavailable"}

    try:
        rows = store.fetchall("SELECT name, applied_at FROM _migrations ORDER BY applied_at", ())
        migrations = [{"name": r["name"], "applied_at": r["applied_at"]} for r in rows]
        return {"migrations": migrations, "count": len(migrations)}
    except Exception:
        return {"migrations": [], "count": 0}


@router.get("/endpoints")
def list_endpoints(request: Request) -> dict[str, Any]:
    """API 端点总览。"""
    app = request.app
    routes_info = []
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            path = route.path
            methods = list(route.methods) if route.methods else []
            # Filter internal/static
            if path.startswith("/api/") or path.startswith("/health/") or path.startswith("/metrics"):
                routes_info.append({
                    "path": path,
                    "methods": [m for m in methods if m != "HEAD" and m != "OPTIONS"],
                })

    return {
        "endpoints": sorted(routes_info, key=lambda r: r["path"]),
        "count": len(routes_info),
    }


@router.post("/log-level")
def set_log_level(req: LogLevelRequest, request: Request) -> dict[str, Any]:
    """动态调整日志级别。"""
    logging.getLogger().setLevel(getattr(logging, req.level))
    # Also update specific EVA loggers
    for name in logging.root.manager.loggerDict:
        if name.startswith("eva."):
            logging.getLogger(name).setLevel(getattr(logging, req.level))

    return {"ok": True, "level": req.level}


@router.get("/versions")
def dependency_versions(request: Request) -> dict[str, Any]:
    """关键依赖版本列表。"""
    versions = {}
    for pkg in ["fastapi", "pydantic", "uvicorn", "yaml", "httpx", "jinja2"]:
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except Exception:
            versions[pkg] = "not_installed"

    import sys
    return {
        "python": sys.version,
        "packages": versions,
    }


@router.get("/env")
def environment_info(request: Request) -> dict[str, Any]:
    """环境变量查看（脱敏 — 只显示 key，不显示 value）。"""
    import os

    # Only show EVA-related vars
    eva_vars = {k: "***" if "KEY" in k or "TOKEN" in k or "SECRET" in k else v[:30]
                for k, v in sorted(os.environ.items())
                if k.startswith("EVA_")}

    return {
        "env_vars": list(eva_vars.keys()),
        "count": len(eva_vars),
    }
