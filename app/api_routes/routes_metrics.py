"""System metrics endpoint — key observability indicators.

GET /metrics — JSON payload with queue sizes, memory stats,
              agent execution timings, and scheduler health.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger("eva.metrics")

_START_TIME = time.time()


@router.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    container = request.app.state.container
    ss = container.system_state
    now = time.time()

    payload: dict[str, Any] = {
        "uptime_seconds": int(now - _START_TIME),
        "timestamp": now,
    }

    # ── queue health ──
    payload["queues"] = {
        "pending_events": container.event_bus.size(),
        "pending_results": container.result_registry.size(),
    }
    try:
        payload["event_bus"] = container.event_bus.stats()
    except Exception:
        logger.warning("could not collect event_bus stats", exc_info=True)
        payload["event_bus"] = {}

    # ── memory ──
    try:
        tm = container.tiered_memory
        payload["memory"] = tm.stats() if tm else {}
    except Exception:
        payload["memory"] = {}

    # ── agents ──
    payload["agents"] = {
        "registered": container.registry.list_agents(),
        "count": len(container.registry.list_agents()),
    }

    # ── scheduler ──
    try:
        payload["scheduler"] = {
            "jobs": container.scheduler.list_jobs(),
        }
    except Exception:
        payload["scheduler"] = {"jobs": []}

    # ── policy ──
    try:
        if container.policy_engine:
            ps = container.policy_engine.get_state()
            payload["policy"] = {
                "state": ps.get("state_machine", {}).get("current", "unknown"),
                "active_tokens": ps.get("tokens", {}).get("active_tokens", 0),
            }
    except Exception:
        payload["policy"] = {"state": "unknown"}

    # ── cognition loop ──
    payload["cognition"] = {
        "focus": ss.get("focus", "idle"),
        "mode": ss.get("mode", "active"),
        "active_tasks": len(ss.get("active_tasks", [])),
        "last_loop_at": ss.get("last_loop_at"),
    }

    # ── diagnostic ──
    diag = ss.get("diagnostic", {})
    if diag:
        payload["health"] = {
            "score": diag.get("score", 0),
            "overall": diag.get("overall", "unknown"),
        }

    # ── rate limiter ──
    try:
        limiter = request.app.state.rate_limiter
        if limiter:
            payload["rate_limiter"] = limiter.stats()
    except Exception:
        pass

    return payload
