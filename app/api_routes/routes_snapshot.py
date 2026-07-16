from fastapi import APIRouter, Request
import logging


router = APIRouter()


@router.get("/api/debug/snapshot")
def debug_snapshot(request: Request) -> dict:
    container = request.app.state.container
    snapshot_store = container["snapshot_store"]
    snapshot = snapshot_store.load_latest()
    return {"snapshot": snapshot}


@router.post("/api/debug/snapshot/save")
def save_snapshot_now(request: Request) -> dict:
    logger = logging.getLogger("eva.api.snapshot")
    container = request.app.state.container
    save_runtime_snapshot = container["save_runtime_snapshot"]
    save_runtime_snapshot()
    logger.info("snapshot saved via api")
    return {"ok": True, "message": "snapshot saved"}
