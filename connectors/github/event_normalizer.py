"""Convert raw GitHub webhook/poller payloads into EVA Event objects."""

from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
import logging
from typing import Any

from event.event_schema import Event
from event.codec import source_event_identity, UnsupportedEvent

logger = logging.getLogger(__name__)

# GitHub event header → EVA EventType
_EVENT_TYPE_MAP: dict[str, str] = {
    "push": "github_push",
    "pull_request": "github_pr",
    "issues": "github_issue",
    "workflow_run": "github_workflow",
}


def _build_source(repo: dict[str, Any]) -> str:
    full_name = repo.get("full_name", "") if isinstance(repo, dict) else ""
    return f"github:{full_name}" if full_name else "github"


def normalize_webhook_payload(
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
) -> Event:
    if event_type not in _EVENT_TYPE_MAP:
        raise UnsupportedEvent(f"unsupported GitHub webhook type: {event_type}")
    if not isinstance(payload, dict) or not delivery_id:
        raise ValueError("webhook requires an object payload and delivery ID")
    eva_type = _EVENT_TYPE_MAP[event_type]
    repo = payload.get("repository", {})
    source = _build_source(repo)
    origin = f"webhook:{delivery_id}"
    observed_at = datetime.now(timezone.utc)

    evt = Event(
        id=source_event_identity(source, eva_type, origin), source_event_id=origin,
        type=eva_type,  # type: ignore[arg-type]
        source=source,
        timestamp=observed_at,
        payload={
            **deepcopy(payload),
            "_github_event": event_type,
            "_delivery_id": delivery_id,
            "_normalized_at": observed_at.isoformat(),
        },
    )
    logger.debug("normalized webhook %s → %s", delivery_id, evt.id)
    return evt


def normalize_poller_payload(
    event_kind: str,
    repo_full_name: str,
    item: dict[str, Any],
) -> Event:
    if event_kind not in {"pr", "issue"} or not isinstance(item, dict) or not repo_full_name:
        raise UnsupportedEvent("unsupported GitHub poll kind or invalid item/source")
    eva_type = f"github_{event_kind}"
    source = f"github:{repo_full_name}"
    # Exact source observation identity: unchanged content is stable even if the
    # provider omitted id/updated_at. Changed content is a different observation.
    content_hash = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    origin = f"poll:{event_kind}:{content_hash}"
    evt = Event(
        id=source_event_identity(source, eva_type, origin), source_event_id=origin,
        type=eva_type,  # type: ignore[arg-type]
        source=source,
        timestamp=datetime.now(timezone.utc),
        payload={
            "_github_event": event_kind,
            "_polled_at": datetime.now(timezone.utc).isoformat(),
            "repository": {"full_name": repo_full_name},
            "action": item.get("state", ""),
            "sender": deepcopy(item.get("user", {})),
            event_kind: deepcopy(item),
        },
    )
    return evt
