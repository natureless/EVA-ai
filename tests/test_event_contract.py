"""Version, identity and provenance invariants across actual event boundaries."""

from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from event.codec import LEGACY_EVENT_MAP, decode_event, encode_event, from_legacy_event, source_event_identity, to_legacy_event
from event.contracts import EventEnvelope
from event.event_bus import EventBus
from event.event_schema import Event
from memory.memory_api import MemoryAPI
from memory.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    store = SQLiteStore(tmp_path / "events.db")
    store.init_db()
    yield store
    store.close()


@pytest.mark.parametrize("kind", LEGACY_EVENT_MAP)
def test_supported_types_round_trip_through_bus_and_storage(kind, store):
    event = Event(type=kind, source="test", timestamp=datetime(2024, 1, 2, tzinfo=timezone(timedelta(hours=8))),
                  source_event_id="occurrence", causation_id="parent", correlation_id=None,
                  session_id="session", subject_id="eva-test", confidence=.6, priority=.7,
                  sensitivity="sensitive", compatibility={"source": "fixture"}, payload={"nested": [1, {"x": True}]})
    wire = from_legacy_event(event)
    assert wire.event_type == LEGACY_EVENT_MAP[kind]
    assert to_legacy_event(wire).model_dump() == event.model_dump()
    assert decode_event(json.loads(encode_event(event))) == event
    bus = EventBus(s5_store=SimpleNamespace(store=store))
    assert bus.publish(event)
    assert bus.consume(0) == event
    bus.task_done()
    assert MemoryAPI(store).get_event(event.id) == event
    assert store.fetchone("SELECT timestamp FROM events WHERE id = ?", (event.id,))["timestamp"] == event.timestamp.isoformat()
    assert MemoryAPI(store).get_recent_events()[0]["event_contract"] is not None


@pytest.mark.parametrize("version", ["2.0", "0.1", "1", 1, True, None, ""])
def test_unknown_version_fails_before_admission_and_store_effects(version, store):
    data = Event(type="user_message", source="user").model_dump()
    data["schema_version"] = version
    with pytest.raises(ValueError):
        Event.model_validate(data)
    with pytest.raises(ValueError):
        decode_event(data)
    tampered = Event.model_construct(**data)
    bus = EventBus(s5_store=SimpleNamespace(store=store))
    with pytest.raises(ValueError):
        bus.publish(tampered)
    assert bus.size() == 0 and bus.stats()["published"] == 0
    assert store.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0


def test_legacy_read_is_deterministic_and_marks_naive_time_assumption():
    old = {"id": "historical-id", "type": "user_message", "source": "user", "timestamp": "2024-01-02T03:04:05",
           "payload": '{"text":"old"}', "correlation_id": None, "status": "captured"}
    first = decode_event(old)
    assert first == decode_event(old)
    assert first.id == "historical-id" and first.correlation_id is None
    assert first.compatibility == {"source_schema": "unversioned-stable", "timestamp_assumed_utc": True}
    assert first.timestamp == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert "schema_version" not in old and isinstance(old["payload"], str)
    for missing in ("id", "timestamp", "source", "type"):
        with pytest.raises(ValueError):
            decode_event({key: value for key, value in old.items() if key != missing})


def test_invalid_contract_never_falls_back_to_legacy_columns(store):
    event = Event(type="user_message", source="user", payload={"text": "actual"})
    MemoryAPI(store).append_event(event)
    row = store.fetchone("SELECT * FROM events WHERE id=?", (event.id,))
    for field, value in (("id", "other"), ("type", "system_tick"), ("payload", '{}'), ("timestamp", "2020-01-01T00:00:00Z")):
        with pytest.raises(ValueError, match="disagrees"):
            decode_event({**row, field: value})
    for wire in ('{"schema_version":"2.0"}', '{}', '[]', '{bad'):
        with pytest.raises(ValueError):
            decode_event({**row, "event_contract": wire})


def test_migration_keeps_original_legacy_columns(store):
    from core.migration import MigrationRunner
    old = ("old", "system_tick", "scheduler", "2024-01-01T00:00:00+00:00", '{}', None, "captured")
    store.execute("INSERT INTO events (id,type,source,timestamp,payload,correlation_id,status) VALUES (?,?,?,?,?,?,?)", old)
    before = store.fetchone("SELECT * FROM events WHERE id='old'")
    assert MigrationRunner(store).migrate() == []
    assert store.fetchone("SELECT * FROM events WHERE id='old'") == before
    assert MemoryAPI(store).get_event("old").id == "old"


def test_copy_migration_backfills_multiple_batches_without_rewriting_history(tmp_path):
    import sqlite3
    from scripts.migrate_event_contracts import migrate_copy, backfill, file_hash
    source, target = tmp_path / "old.db", tmp_path / "copy.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE events (id TEXT PRIMARY KEY,type TEXT,source TEXT,timestamp TEXT,payload TEXT,correlation_id TEXT,status TEXT)")
    rows = [(f"old-{i}", "user_message", "user", "2024-01-01T00:00:00", '{"text":"old"}', None, "captured") for i in range(9)]
    rows += [(None, "unknown_archive_type", "import", "bad", '{}', None, "imported")]
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    before = file_hash(source)
    result = migrate_copy(source, target)
    assert result["backfilled"] == 9 and result["unsupported_or_invalid"] == 1
    assert result["source_file_unchanged"] and result["status"] == "partial"
    with pytest.raises(FileExistsError):
        migrate_copy(source, target)
    with pytest.raises(FileExistsError):
        migrate_copy(source, source)
    assert file_hash(source) == before
    copied = SQLiteStore(target)
    try:
        second = backfill(copied, batch_size=2)
        assert second == {"scanned": 10, "backfilled": 0, "existing_valid": 9, "unsupported_or_invalid": 1}
        for old in rows[:-1]:
            assert tuple(copied.fetchone("SELECT id,type,source,timestamp,payload,correlation_id,status FROM events WHERE id=?", (old[0],)).values()) == old
            assert MemoryAPI(copied).get_event(old[0]).compatibility["timestamp_assumed_utc"] is True
    finally:
        copied.close()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_non_json_payload_is_not_admitted(invalid):
    with pytest.raises(ValueError, match="finite JSON"):
        Event(type="user_message", source="user", payload={"value": invalid})


def test_source_occurrence_ids_are_scoped_and_not_random():
    key = source_event_identity("github:x/y", "github_push", "webhook:d1")
    assert key == source_event_identity("github:x/y", "github_push", "webhook:d1")
    variants = [source_event_identity("github:x/z", "github_push", "webhook:d1"),
                source_event_identity("github:x/y", "github_pr", "webhook:d1"),
                source_event_identity("github:x/y", "github_push", "webhook:d2"),
                source_event_identity("github:x/y", "github_push", "webhook:d1", subject_id="other")]
    assert key not in variants and len(set(variants)) == 4


def test_admission_owns_payload_and_metadata_snapshot(store):
    bus = EventBus(s5_store=SimpleNamespace(store=store))
    event = Event(type="user_message", source="user", correlation_id="accepted-task", payload={"items": [{"text": "accepted"}]})
    expected = event.model_dump()
    assert bus.publish(event)
    event.payload["items"][0]["text"] = "changed"
    event.correlation_id = "other-task"
    consumed = bus.consume(0)
    bus.task_done()
    assert consumed is not event and consumed.model_dump() == expected
    assert MemoryAPI(store).get_event(event.id).model_dump() == expected


def test_github_identity_and_reserved_metadata():
    from connectors.github.event_normalizer import normalize_webhook_payload, normalize_poller_payload
    raw = {"repository": {"full_name": "x/y"}, "_github_event": "issues", "_delivery_id": "spoofed"}
    first = normalize_webhook_payload("push", "d1", raw)
    assert first.id == normalize_webhook_payload("push", "d1", raw).id
    assert first.payload["_github_event"] == "push" and first.payload["_delivery_id"] == "d1"
    assert raw["_delivery_id"] == "spoofed"
    item = {"number": 1, "updated_at": "2024-01-02", "title": "old"}
    polled = normalize_poller_payload("issue", "x/y", item)
    assert polled.id == normalize_poller_payload("issue", "x/y", dict(reversed(list(item.items())))).id
    item["title"] = "new"
    assert polled.payload["issue"]["title"] == "old"
    assert polled.id != normalize_poller_payload("issue", "x/y", item).id


def test_scheduler_observation_identity_survives_reconstruction():
    from runtime.scheduler import RuntimeScheduler
    bus = EventBus()
    scheduler = RuntimeScheduler(bus, lambda: None, {})
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    scheduler._publish_system_tick(stamp)
    scheduler._publish_system_tick(stamp)
    scheduler._publish_maintenance(stamp)
    first, second, maintenance = bus.drain()
    assert first.id == second.id and first.source_event_id == second.source_event_id
    assert first.id != maintenance.id
    assert decode_event(first.model_dump()) == first


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
def test_http_rejects_unknown_version_before_receipt_reservation(path):
    from app.api_routes import routes_chat
    bus, registry = EventBus(), Mock()
    app = FastAPI()
    app.state.container = SimpleNamespace(event_bus=bus, result_registry=registry)
    app.include_router(routes_chat.router)
    with TestClient(app) as client:
        assert client.post(path, json={"text": "hello", "schema_version": "2.0"}).status_code == 422
    registry.create.assert_not_called()
    assert bus.size() == 0


def test_user_source_id_is_stable_and_absent_ids_remain_distinct():
    from app.api_routes.routes_chat import ChatRequest, _chat_event
    request = ChatRequest(text="same occurrence", source_event_id="client-1")
    first, second = _chat_event(request, "c1"), _chat_event(request, "c2", stream=True)
    assert first.id == second.id and first.correlation_id != second.correlation_id
    assert first.source_event_id == "client-1"
    assert _chat_event(ChatRequest(text="hello"), "c1").id != _chat_event(ChatRequest(text="hello"), "c1").id


def test_signed_webhook_rejects_unknown_type_without_poisoning_delivery_retry(monkeypatch):
    import hashlib
    import hmac
    from app.api_routes import routes_github
    monkeypatch.setattr(routes_github, "settings", SimpleNamespace(github_webhook_secret="test-secret"))
    bus = EventBus()
    app = FastAPI()
    app.state.container = SimpleNamespace(event_bus=bus)
    app.include_router(routes_github.router)
    body = json.dumps({"repository": {"full_name": "x/y"}}).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "unknown", "X-GitHub-Delivery": "same-delivery", "X-Hub-Signature-256": signature}
    with TestClient(app) as client:
        assert client.post("/api/webhooks/github", content=body, headers=headers).status_code == 422
        assert bus.size() == 0 and not getattr(app.state, "_github_dedup_set", set())
        headers["X-GitHub-Event"] = "push"
        response = client.post("/api/webhooks/github", content=body, headers=headers)
        assert response.status_code == 200
        event = bus.drain()[0]
        assert event.id == response.json()["event_id"]
        assert event.source_event_id == "webhook:same-delivery"
        assert event.schema_version == "1.0" and event.type == "github_push"


@pytest.mark.asyncio
async def test_adapter_preserves_metadata_and_rejects_unroutable_before_effects(tmp_path):
    from packages.kernel.event_store import EventStore
    from packages.kernel.event_bus_adapter import EventBusAdapter
    bus = EventBus()
    store = EventStore(tmp_path / "mvsc.db")
    adapter = EventBusAdapter(bus, store)
    notified = Mock()
    adapter.subscribe("", notified)
    try:
        invalid = EventEnvelope(event_type="action.tool_started", source="test")
        with pytest.raises(ValueError, match="no stable consumer"):
            await adapter.publish(invalid)
        with pytest.raises(ValueError):
            await adapter.publish(invalid.model_copy(update={"schema_version": "2.0"}))
        assert store.get_latest_sequence() == 0 and bus.size() == 0
        notified.assert_not_called()
        event = EventEnvelope(event_type="perception.github_issue", source="test", source_event_id="source-1",
                              causation_id="parent", session_id="session", confidence=.4, priority=.9, status="observed")
        assert await adapter.publish(event)
        received = await adapter.consume(.1)
        bus.task_done()
        assert received.model_dump() == event.model_dump()
        assert store.replay()[0].model_dump() == event.model_dump()
        bad = event.model_copy(update={"schema_version": "2.0"})
        with pytest.raises(ValueError):
            store.append_batch([EventEnvelope(event_type="test", source="test"), bad])
        assert store.get_latest_sequence() == 1
    finally:
        store.close()
