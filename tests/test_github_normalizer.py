"""Tests for connectors.github.event_normalizer — webhook & poller payload → Event."""



import pytest

from connectors.github.event_normalizer import (
    normalize_webhook_payload,
    normalize_poller_payload,
)


# ── webhook normalization ────────────────────────────────────

WEBHOOK_PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "repository": {"full_name": "eva-ai/test-repo", "id": 123},
    "commits": [{"id": "abc123", "message": "fix bug"}],
}


def test_normalize_push_webhook():
    event = normalize_webhook_payload("push", "delivery-001", WEBHOOK_PUSH_PAYLOAD)
    assert event.type == "github_push"
    assert event.source == "github:eva-ai/test-repo"
    assert event.payload["_github_event"] == "push"
    assert event.payload["_delivery_id"] == "delivery-001"
    assert event.payload["ref"] == "refs/heads/main"


def test_normalize_pr_webhook():
    payload = {
        "action": "opened",
        "repository": {"full_name": "eva-ai/test-repo"},
        "pull_request": {"number": 42, "title": "Add feature X"},
    }
    event = normalize_webhook_payload("pull_request", "del-002", payload)
    assert event.type == "github_pr"
    assert event.source == "github:eva-ai/test-repo"
    assert event.payload["action"] == "opened"


def test_normalize_issue_webhook():
    event = normalize_webhook_payload(
        "issues", "del-003",
        {"repository": {"full_name": "eva-ai/backend"}, "issue": {"number": 7}},
    )
    assert event.type == "github_issue"
    assert event.source == "github:eva-ai/backend"


def test_normalize_workflow_webhook():
    event = normalize_webhook_payload(
        "workflow_run", "del-004",
        {"repository": {"full_name": "eva-ai/ci"}, "workflow_run": {"status": "completed"}},
    )
    assert event.type == "github_workflow"


def test_unknown_webhook_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported GitHub"):
        normalize_webhook_payload("unknown_type", "del-005", WEBHOOK_PUSH_PAYLOAD)


def test_webhook_missing_repo_key():
    event = normalize_webhook_payload("push", "del-006", {"ref": "refs/heads/dev"})
    assert event.source == "github"
    assert event.payload["_delivery_id"] == "del-006"


def test_webhook_repo_not_dict():
    event = normalize_webhook_payload("push", "del-007", {"repository": None})
    assert event.source == "github"


def test_webhook_preserves_payload_fields():
    nested = {"nested": {"deep": True}}
    event = normalize_webhook_payload(
        "push", "del-008",
        {"repository": {"full_name": "x/y"}, "custom": nested},
    )
    assert event.payload["custom"] == nested


# ── poller normalization ─────────────────────────────────────

def test_normalize_poller_pr():
    item = {
        "number": 1,
        "title": "Fix typo",
        "state": "open",
        "user": {"login": "dev1"},
        "updated_at": "2026-01-01T00:00:00Z",
    }
    event = normalize_poller_payload("pr", "eva-ai/core", item)
    assert event.type == "github_pr"
    assert event.source == "github:eva-ai/core"
    assert event.payload["_github_event"] == "pr"
    assert event.payload["pr"] == item
    assert event.payload["action"] == "open"


def test_normalize_poller_issue():
    item = {"number": 99, "title": "Memory leak", "state": "open", "user": {"login": "qa"}}
    event = normalize_poller_payload("issue", "eva-ai/core", item)
    assert event.type == "github_issue"
    assert event.payload["issue"] == item
    assert event.payload["repository"]["full_name"] == "eva-ai/core"


def test_poller_event_has_timestamp():
    event = normalize_poller_payload("pr", "a/b", {"state": "closed", "user": {}})
    assert event.timestamp is not None
    assert event.id is not None
