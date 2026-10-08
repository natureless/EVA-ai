"""Explicit server-side file sampling; clients cannot supply checker facts/specs."""

from contextlib import contextmanager
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/tool-observations", tags=["tool-observations"])


class ReconcileToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=256)
    expected_count: int = Field(ge=0, lt=32, strict=True)


def _journal(request):
    container = request.app.state.container
    journal = container.integrations.processing_episodes
    if journal is None:
        raise HTTPException(503, "processing Episodes are not enabled")
    return container, journal


@contextmanager
def observation_errors():
    try:
        yield
    except ValueError:
        raise HTTPException(409, "tool observation binding or state conflict") from None
    except (RuntimeError, sqlite3.Error):
        raise HTTPException(503, "tool observation storage unavailable") from None


@router.get("/{action_id}")
def observation_history(
    action_id: str,
    request: Request,
    response: Response,
    event_id: str = Query(min_length=1, max_length=256),
):
    _, journal = _journal(request)
    response.headers["Cache-Control"] = "no-store"
    with observation_errors():
        return journal.observation_history(event_id, action_id)


@router.post("/{action_id}", status_code=201)
def reconcile_tool(
    action_id: str, req: ReconcileToolRequest, request: Request, response: Response
):
    container, journal = _journal(request)
    if not container.runtime.controller.accepting:
        raise HTTPException(503, "runtime not accepting file observations")
    response.headers["Cache-Control"] = "no-store"
    with observation_errors():
        return journal.reconcile_tool(
            req.event_id, action_id, expected_count=req.expected_count
        )
