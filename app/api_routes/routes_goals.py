"""Authenticated goal registration and server-side receipt reconciliation.

There is intentionally no endpoint that accepts observations or completed status.
"""

from contextlib import contextmanager
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/goals", tags=["business-goals"])


class ConditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(
        pattern=r"^checks\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$", max_length=256
    )
    operator: Literal["equals", "not_equals", "contains", "gte", "lte", "truthy"] = (
        "equals"
    )
    expected: Any = None


class CreateGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=1000)
    success_conditions: list[ConditionRequest] = Field(min_length=1, max_length=16)
    deadline: AwareDatetime | None = None
    verification: dict[str, Any] | None = None


class CancelGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)


@contextmanager
def goal_errors():
    try:
        yield
    except KeyError:
        raise HTTPException(404, "goal not found") from None
    except ValueError:
        raise HTTPException(409, "goal state or contract conflict") from None
    except (sqlite3.Error, RuntimeError):
        raise HTTPException(503, "goal storage unavailable") from None


def _store(request):
    container = request.app.state.container
    store = container.integrations.business_goals
    if store is None:
        raise HTTPException(503, "business goals are not enabled")
    return container, store


def _reconcile(container, store, goal):
    view = container.runtime.results.lookup(goal["task_id"])
    if view["state"] == "terminal":
        store.record_receipt(
            {
                **view["payload"],
                "task_id": goal["task_id"],
                "event_id": view["event_id"],
            }
        )
    elif view["state"] == "missing":
        journal = container.integrations.processing_episodes
        try:
            receipt = (
                journal.receipt_for_task(goal["task_id"])
                if journal is not None
                else None
            )
        except ValueError:
            raise RuntimeError("durable processing receipt unavailable") from None
        if receipt is not None:
            store.record_receipt(receipt)
    return store.get(goal["goal_id"])


@router.post("", status_code=201)
def create_goal(req: CreateGoalRequest, request: Request):
    container, store = _store(request)
    if not container.runtime.controller.accepting:
        raise HTTPException(503, "runtime not accepting goals")
    view = container.runtime.results.lookup(req.task_id)
    if view["state"] == "missing" or not view.get("event_id"):
        raise HTTPException(404, "registered task not found or receipt expired")
    with goal_errors():
        goal = store.create(
            task_id=req.task_id,
            source_event_id=view["event_id"],
            description=req.description,
            success_conditions=[c.model_dump() for c in req.success_conditions],
            deadline=req.deadline,
            verification=req.verification,
        )
        # Creation and registry reads are not a distributed transaction. A second
        # read covers completion between admission lookup and goal insertion.
        return _reconcile(container, store, goal)


@router.get("/by-task/{task_id}")
def goal_for_task(task_id: str, request: Request):
    container, store = _store(request)
    with goal_errors():
        return _reconcile(container, store, store.get_by_task(task_id))


@router.get("/graph")
def goal_graph(
    request: Request,
    response: Response,
    limit: int = Query(40, ge=1, le=40),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    q: str = Query("", max_length=200),
):
    _, store = _store(request)
    response.headers["Cache-Control"] = "no-store"
    with goal_errors():
        return store.graph(limit=limit, offset=offset, query=q)


@router.get("/{goal_id}/history")
def goal_history(
    goal_id: str,
    request: Request,
    response: Response,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=2_147_483_647),
):
    _, store = _store(request)
    response.headers["Cache-Control"] = "no-store"
    with goal_errors():
        return store.verification_history(goal_id, limit=limit, offset=offset)


@router.get("/{goal_id}/episode")
def goal_episode(goal_id: str, request: Request, response: Response):
    _, store = _store(request)
    response.headers["Cache-Control"] = "no-store"
    with goal_errors():
        return store.episode_reference(goal_id)


@router.get("/{goal_id}")
def get_goal(goal_id: str, request: Request):
    container, store = _store(request)
    with goal_errors():
        return _reconcile(container, store, store.get(goal_id))


@router.post("/{goal_id}/cancel")
def cancel_goal(goal_id: str, req: CancelGoalRequest, request: Request):
    _, store = _store(request)
    with goal_errors():
        return store.cancel(goal_id, expected_version=req.expected_version)


@router.post("/{goal_id}/verify")
def verify_goal(goal_id: str, req: CancelGoalRequest, request: Request):
    container, store = _store(request)
    if not container.runtime.controller.accepting:
        raise HTTPException(503, "runtime not accepting verification")
    with goal_errors():
        _reconcile(container, store, store.get(goal_id))
        return store.verify(goal_id, expected_version=req.expected_version)
