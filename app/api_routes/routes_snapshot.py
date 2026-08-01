from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/debug/snapshot")
def debug_snapshot(request: Request) -> dict[str, Any]:
    """获取最新快照。"""
    snapshot = request.app.state.container.snapshot_store.load_latest()
    return {"snapshot": snapshot}


@router.post("/api/debug/snapshot/save")
def save_snapshot_now(request: Request) -> dict[str, Any]:
    """手动触发快照保存。"""
    logger = logging.getLogger("eva.api.snapshot")
    request.app.state.container.save_runtime_snapshot()
    logger.info("snapshot saved via api")
    return {"ok": True, "message": "snapshot saved", "saved_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/debug/snapshot/list")
def list_snapshots(request: Request) -> dict[str, Any]:
    """列出所有快照文件。"""
    from app.config import settings

    snap_dir = Path(settings.snapshot_dir)
    if not snap_dir.exists():
        return {"snapshots": [], "count": 0}

    files = sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "snapshots": [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
            for f in files[:20]
        ],
        "count": len(files),
        "directory": str(snap_dir),
    }


@router.get("/api/debug/snapshot/status")
def snapshot_status(request: Request) -> dict[str, Any]:
    """快照系统状态。"""
    container = request.app.state.container
    ss = container.system_state

    return {
        "last_snapshot_at": ss.get("last_snapshot_at"),
        "snapshot_ready": ss.get("snapshot_ready", False),
        "snapshot_interval_sec": container.settings.scheduler_snapshot_interval_sec,
        "world_entities": container.world_model.entity_count if container.world_model else 0,
        "world_edges": container.world_model.edge_count if container.world_model else 0,
    }
