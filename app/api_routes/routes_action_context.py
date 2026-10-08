"""Read-only agent input/intent inspection; no raw context or claim capabilities."""

import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, Response

from memory.action_context_graph import project_action_context

router = APIRouter(prefix="/api/action-context", tags=["action-context"])


@router.get("/{task_id}")
def get_action_context(task_id: str, request: Request):
    if not 1 <= len(task_id) <= 256:
        raise HTTPException(422, "invalid task identity")
    store = request.app.state.container.integrations.durable_requests
    if store is None:
        raise HTTPException(503, "durable requests are not enabled")
    try:
        view = store.agent_context_for_task(task_id)
    except (ValueError, RuntimeError, KeyError, sqlite3.Error):
        raise HTTPException(503, "action context unavailable") from None
    if view is None:
        raise HTTPException(404, "agent input intent not found")
    return view


@router.get("/{task_id}/graph")
def get_action_context_graph(
    task_id: str,
    request: Request,
    response: Response,
    event_id: str = Query(min_length=1, max_length=256),
):
    view = get_action_context(task_id, request)
    if view["event_id"] != event_id:
        raise HTTPException(409, "action context source mismatch")
    try:
        graph = project_action_context(view)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "action input graph unavailable") from None
    response.headers["Cache-Control"] = "private, no-store"
    return graph
