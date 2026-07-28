from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/debug/snapshot")
def debug_snapshot(request: Request) -> dict[str, Any]:
    snapshot = request.app.state.container.snapshot_store.load_latest()
    return {"snapshot": snapshot}


@router.post("/api/debug/snapshot/save")
def save_snapshot_now(request: Request) -> dict[str, Any]:
    logger = logging.getLogger("eva.api.snapshot")
    request.app.state.container.save_runtime_snapshot()
    logger.info("snapshot saved via api")
    return {"ok": True, "message": "snapshot saved"}
