"""HMAC-SHA256 verification and webhook processing."""

import hashlib
import hmac
import json
import logging
from typing import Any

from event.event_bus import EventBus

logger = logging.getLogger(__name__)


def verify_signature(secret: str, signature_header: str, body: bytes) -> bool:
    """Constant-time HMAC-SHA256 verification.

    Args:
        secret: The webhook secret configured in GitHub.
        signature_header: Value of X-Hub-Signature-256 header ("sha256=...")
        body: Raw request body bytes.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def process_webhook(
    body: bytes,
    event_type: str,
    delivery_id: str,
    secret: str,
    event_bus: EventBus,
    dedup_set: set[str],
) -> dict[str, Any]:
    """Verify, dedup, normalize, and publish a GitHub webhook event.

    Returns a status dict:
        {"status": "ok", "event_id": "..."}
        {"status": "duplicate", "delivery_id": "..."}
        {"status": "invalid"}
    """
    if delivery_id in dedup_set:
        logger.debug("duplicate webhook delivery %s", delivery_id)
        return {"status": "duplicate", "delivery_id": delivery_id}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("invalid JSON in webhook body")
        return {"status": "invalid"}

    from connectors.github.event_normalizer import normalize_webhook_payload

    try:
        event = normalize_webhook_payload(event_type, delivery_id, payload)
    except ValueError:
        return {"status": "unsupported", "delivery_id": delivery_id}
    if not event_bus.publish(event):
        return {"status": "rejected", "delivery_id": delivery_id}
    if len(dedup_set) >= 10000:
        dedup_set.clear()
    dedup_set.add(delivery_id)
    logger.info("webhook published: %s → %s", delivery_id, event.id)
    return {"status": "ok", "event_id": event.id, "delivery_id": delivery_id}
