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


@router.get("/resources")
def system_resources(request: Request) -> dict[str, Any]:
    """系统资源使用 — 内存、CPU、磁盘。"""
    import os

    info: dict[str, Any] = {}

    # Memory (via psutil if available)
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem = process.memory_info()
        info["memory"] = {
            "rss_mb": round(mem.rss / 1024 / 1024, 1),
            "vms_mb": round(mem.vms / 1024 / 1024, 1),
            "percent": round(process.memory_percent(), 1),
        }
        info["cpu"] = {
            "percent": round(process.cpu_percent(interval=0.1), 1),
            "threads": process.num_threads(),
        }
    except ImportError:
        info["memory"] = {"status": "psutil_not_installed"}
        info["cpu"] = {"status": "psutil_not_installed"}

    # Disk
    try:
        import shutil
        usage = shutil.disk_usage("data")
        info["disk"] = {
            "total_gb": round(usage.total / 1024**3, 1),
            "used_gb": round(usage.used / 1024**3, 1),
            "free_gb": round(usage.free / 1024**3, 1),
        }
    except Exception:
        info["disk"] = {"status": "unavailable"}

    return info


@router.get("/data-usage")
def data_usage(request: Request) -> dict[str, Any]:
    """数据目录文件大小统计。"""
    from pathlib import Path

    data_dir = Path("data")
    if not data_dir.exists():
        return {"files": [], "total_size_mb": 0}

    files = []
    total = 0
    for f in sorted(data_dir.rglob("*")):
        if f.is_file():
            size = f.stat().st_size
            total += size
            files.append({
                "name": str(f.relative_to(data_dir)),
                "size_kb": round(size / 1024, 1),
            })

    return {
        "files": files[:20],
        "total_files": len(files),
        "total_size_mb": round(total / 1024**2, 1),
    }
