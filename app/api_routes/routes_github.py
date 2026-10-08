"""GitHub webhook receiver route."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.api_routes.admission import admission_rejected, publish_event
from event.codec import UnsupportedEvent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(..., alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(..., alias="X-GitHub-Delivery"),
) -> Any:
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=501, detail="webhook secret not configured")

    body = await request.body()

    from connectors.github.webhook_handler import verify_signature

    if not verify_signature(settings.github_webhook_secret, x_hub_signature_256, body):
        raise HTTPException(status_code=401, detail="invalid signature")

    container = request.app.state.container

    dedup_set: set[str] = getattr(request.app.state, "_github_dedup_set", set())
    if x_github_delivery in dedup_set:
        return {"status": "duplicate", "delivery_id": x_github_delivery}

    import json

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    from connectors.github.event_normalizer import normalize_webhook_payload

    try:
        event = normalize_webhook_payload(x_github_event, x_github_delivery, payload)
    except (UnsupportedEvent, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if rejection := publish_event(container, event):
        return admission_rejected(rejection, delivery_id=x_github_delivery)

    # Only admitted deliveries are deduplicated. A rejected delivery must remain
    # retryable when queue capacity returns or a new runtime starts.
    if len(dedup_set) >= 10000:
        dedup_set.clear()
    dedup_set.add(x_github_delivery)
    request.app.state._github_dedup_set = dedup_set

    logger.info("github webhook: %s %s → %s", x_github_event, x_github_delivery, event.id)
    return {"status": "ok", "event_id": event.id, "delivery_id": x_github_delivery}


@router.get("/api/github/status")
def github_status(request: Request) -> dict[str, Any]:
    """GitHub 连接器状态。"""
    container = request.app.state.container

    return {
        "webhook_configured": bool(container.settings.github_webhook_secret),
        "poll_configured": bool(container.settings.github_api_token and container.settings.github_poll_repos),
        "poll_repos": container.settings.github_poll_repos,
        "poll_interval_sec": container.settings.github_poll_interval_sec,
        "poller_active": container.github_poller is not None,
    }
