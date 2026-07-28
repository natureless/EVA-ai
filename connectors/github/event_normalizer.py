"""Convert raw GitHub webhook/poller payloads into EVA Event objects."""

from datetime import datetime, timezone
import logging
from typing import Any

from event.event_schema import Event

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
    eva_type = _EVENT_TYPE_MAP.get(event_type, "github_push")
    repo = payload.get("repository", {})
    source = _build_source(repo)

    evt = Event(
        type=eva_type,  # type: ignore[arg-type]
        source=source,
        timestamp=datetime.now(timezone.utc),
        payload={
            "_github_event": event_type,
            "_delivery_id": delivery_id,
            "_normalized_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        },
    )
    logger.debug("normalized webhook %s → %s", delivery_id, evt.id)
    return evt


def normalize_poller_payload(
    event_kind: str,
    repo_full_name: str,
    item: dict[str, Any],
) -> Event:
    eva_type = f"github_{event_kind}"  # "github_pr" or "github_issue"
    evt = Event(
        type=eva_type,  # type: ignore[arg-type]
        source=f"github:{repo_full_name}",
        timestamp=datetime.now(timezone.utc),
        payload={
            "_github_event": event_kind,
            "_polled_at": datetime.now(timezone.utc).isoformat(),
            "repository": {"full_name": repo_full_name},
            "action": item.get("state", ""),
            "sender": item.get("user", {}),
            event_kind: item,
        },
    )
    return evt
