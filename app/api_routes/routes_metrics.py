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


@router.get("/metrics/prometheus")
def metrics_prometheus(request: Request) -> Any:
    """Prometheus 格式指标端点。

    返回 text/plain 格式的 Prometheus 指标，可直接被 Prometheus 抓取。
    """
    from fastapi.responses import PlainTextResponse

    container = request.app.state.container
    ss = container.system_state
    now = time.time()

    lines = [
        "# HELP eva_uptime_seconds System uptime in seconds",
        "# TYPE eva_uptime_seconds gauge",
        f"eva_uptime_seconds {int(now - _START_TIME)}",
        "",
        "# HELP eva_pending_events Event queue depth",
        "# TYPE eva_pending_events gauge",
        f"eva_pending_events {container.event_bus.size()}",
        "",
        "# HELP eva_pending_results Result registry size",
        "# TYPE eva_pending_results gauge",
        f"eva_pending_results {container.result_registry.size()}",
        "",
        "# HELP eva_events_dropped Events dropped due to overflow",
        "# TYPE eva_events_dropped counter",
        f"eva_events_dropped {container.event_bus.dropped}",
        "",
        "# HELP eva_agents_registered Number of registered agents",
        "# TYPE eva_agents_registered gauge",
        f"eva_agents_registered {len(container.registry.list_agents())}",
        "",
        "# HELP eva_cognition_focus Current focus (1=active, 0=idle)",
        "# TYPE eva_cognition_focus gauge",
        f"eva_cognition_focus {0 if ss.get('focus', 'idle') == 'idle' else 1}",
        "",
        "# HELP eva_stability_score Self-model stability",
        "# TYPE eva_stability_score gauge",
        f"eva_stability_score {ss.get('stability_score', 1.0)}",
        "",
        "# HELP eva_prediction_error Mean prediction error",
        "# TYPE eva_prediction_error gauge",
        f"eva_prediction_error {ss.get('mean_prediction_error', 0.0)}",
        "",
    ]

    # MVSC metrics (when enabled)
    if ss.get("mvsc_feature_flags"):
        lines.extend([
            "# HELP eva_mvsc_tick MVSC cognition tick counter",
            "# TYPE eva_mvsc_tick counter",
            f"eva_mvsc_tick {ss.get('mvsc_tick', 0)}",
            "",
        ])

    # Memory tier stats
    try:
        tm = container.tiered_memory
        if tm:
            stats = tm.stats()
            for tier_name, tier_data in stats.items():
                entries = tier_data.get("entries", tier_data.get("entries_active", 0))
                safe_name = tier_name.lower()
                lines.extend([
                    f"# HELP eva_memory_{safe_name}_entries Memory entries in {tier_name}",
                    f"# TYPE eva_memory_{safe_name}_entries gauge",
                    f"eva_memory_{safe_name}_entries {entries}",
                    "",
                ])
    except Exception:
        pass

    return PlainTextResponse(content="\n".join(lines) + "\n", media_type="text/plain")
