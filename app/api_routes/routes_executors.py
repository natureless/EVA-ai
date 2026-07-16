"""Executor API endpoints.

GET  /api/executors              — list registered executors
GET  /api/executors/audit         — query audit log
POST /api/executors/file/read     — read file (token required)
POST /api/executors/file/write    — write file (token required)
POST /api/executors/file/list     — list directory (token required)
POST /api/executors/code/execute  — execute code (token required)
POST /api/executors/comms/log     — log a message via comms executor
POST /api/executors/comms/notify  — write a notification file
POST /api/executors/comms/alert   — log + write alert notification
"""

import json

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


@router.get("/api/executors")
def list_executors(request: Request):
    execs = request.app.state.container.executors
    return {
        "executors": [
            {"name": e.name, "description": e.description}
            for e in execs.values()
        ]
    }


@router.get("/api/executors/audit")
def audit_log(
    request: Request,
    executor_type: str = "",
    limit: int = 50,
    status: str = "",
):
    audit = request.app.state.container.executor_audit_log
    if audit is None:
        return {"items": [], "summary": {"total": 0}}
    items = audit.query(executor_type=executor_type, limit=limit, status=status)
    counts = audit.count_by_type()
    return {
        "items": items,
        "summary": {"total": len(items), "by_type": counts},
    }


def _executor_action(
    request: Request,
    executor_name: str,
    action: str,
    params: dict[str, object],
) -> dict[str, object]:
    container = request.app.state.container
    executor = container.executors.get(executor_name)
    if executor is None:
        raise HTTPException(status_code=404, detail=f"executor '{executor_name}' not found")

    token_manager = None
    if container.policy_engine:
        token_manager = container.policy_engine.token_manager

    return executor.execute(
        action=action,
        params=params,
        task_id=params.get("task_id", ""),
        token_id=params.get("token_id", ""),
        token_manager=token_manager,
    )


@router.post("/api/executors/file/read")
async def file_read(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    params.setdefault("action", "read")
    return _executor_action(request, "file", "read", params)


@router.post("/api/executors/file/write")
async def file_write(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    params.setdefault("action", "write")
    if not params.get("path") or not params.get("content"):
        raise HTTPException(status_code=400, detail="path and content required")
    return _executor_action(request, "file", "write", params)


@router.post("/api/executors/file/list")
async def file_list(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    return _executor_action(request, "file", "list", params)


@router.post("/api/executors/code/execute")
async def code_execute(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not params.get("code"):
        raise HTTPException(status_code=400, detail="code field required")
    params.setdefault("language", "python")
    return _executor_action(request, "code", "execute", params)


# ── Audit Replay ────────────────────────────────────────────

@router.get("/api/executors/audit/replay")
def audit_replay(
    request: Request,
    task_id: str = "",
    executor_type: str = "",
    limit: int = 20,
):
    """Replay audit trail for a specific task or executor."""
    audit = request.app.state.container.executor_audit_log
    if audit is None:
        return {"items": [], "timeline": []}

    items = audit.query(executor_type=executor_type, limit=limit)

    if task_id:
        items = [i for i in items if i.get("task_id") == task_id]

    timeline = []
    for item in items:
        timeline.append({
            "timestamp": item.get("timestamp", ""),
            "executor": item.get("executor_type", ""),
            "action": item.get("action", ""),
            "status": item.get("status", ""),
            "summary": item.get("result_summary", "")[:200],
            "duration_ms": item.get("duration_ms", 0),
        })

    return {
        "items": items,
        "timeline": sorted(timeline, key=lambda t: t["timestamp"], reverse=True),
        "total": len(items),
    }


# ── Comms Executor Routes ──────────────────────────────────

@router.post("/api/executors/comms/log")
async def comms_log(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not params.get("message"):
        raise HTTPException(status_code=400, detail="message field required")
    return _executor_action(request, "comms", "log", params)


@router.post("/api/executors/comms/notify")
async def comms_notify(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not params.get("message"):
        raise HTTPException(status_code=400, detail="message field required")
    return _executor_action(request, "comms", "notify", params)


@router.post("/api/executors/comms/alert")
async def comms_alert(request: Request):
    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not params.get("message"):
        raise HTTPException(status_code=400, detail="message field required")
    return _executor_action(request, "comms", "alert", params)
