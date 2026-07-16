"""Executor API endpoints.

GET  /api/executors              — list registered executors
GET  /api/executors/audit         — query audit log
POST /api/executors/file/read     — read file (token required)
POST /api/executors/file/write    — write file (token required)
POST /api/executors/file/list     — list directory (token required)
POST /api/executors/code/execute  — execute code (token required)
"""

import json
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


@router.get("/api/executors")
def list_executors(request: Request):
    container = request.app.state.container
    execs = container.get("executors", {})
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
    container = request.app.state.container
    audit = container.get("executor_audit_log")
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
    params: dict,
) -> dict:
    container = request.app.state.container
    execs = container.get("executors", {})
    executor = execs.get(executor_name)
    if executor is None:
        raise HTTPException(status_code=404, detail=f"executor '{executor_name}' not found")

    token_manager = None
    policy_engine = container.get("policy_engine")
    if policy_engine:
        token_manager = policy_engine.token_manager

    result = executor.execute(
        action=action,
        params=params,
        task_id=params.get("task_id", ""),
        token_id=params.get("token_id", ""),
        token_manager=token_manager,
    )
    return result


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
