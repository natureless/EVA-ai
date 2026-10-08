"""EVT-02: durable inbox/outbox admission, leases and idempotency."""

from packages.contracts.events import EventEnvelope
from packages.kernel.inbox_outbox import DurableInboxOutbox
from packages.kernel.event_bus_adapter import EventBusAdapter
from event.event_bus import EventBus


def event(event_id: str = "evt-1") -> EventEnvelope:
    return EventEnvelope(event_id=event_id, event_type="test.input", source="test")


def routable_event(event_id: str = "evt-durable") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="perception.user_message_received",
        source="test",
        payload={"text": "durable"},
    )


def test_inbox_is_durable_and_deduplicated(tmp_path):
    db = tmp_path / "queue.db"
    first = DurableInboxOutbox(db)
    assert first.enqueue_inbox(event(), now=10.0) is True
    assert first.enqueue_inbox(event(), now=11.0) is False
    first.close()

    reopened = DurableInboxOutbox(db)
    claims = reopened.claim_inbox(worker_id="worker-a", now=12.0)
    assert len(claims) == 1
    assert claims[0].event.event_id == "evt-1"
    assert claims[0].attempts == 1
    assert reopened.complete_inbox("evt-1", now=13.0) is True
    assert reopened.complete_inbox("evt-1", now=14.0) is True
    assert reopened.claim_inbox(worker_id="worker-a", now=15.0) == []
    reopened.close()


def test_expired_inbox_lease_can_be_reclaimed(tmp_path):
    store = DurableInboxOutbox(tmp_path / "queue.db")
    store.enqueue_inbox(event(), now=0.0)
    first = store.claim_inbox(worker_id="crashed", lease_seconds=10.0, now=1.0)
    assert first[0].attempts == 1
    assert store.claim_inbox(worker_id="other", now=5.0) == []
    second = store.claim_inbox(worker_id="other", now=11.0)
    assert second[0].event.event_id == "evt-1"
    assert second[0].attempts == 2
    store.close()


def test_inbox_failure_can_be_retried_or_terminally_failed(tmp_path):
    store = DurableInboxOutbox(tmp_path / "queue.db")
    store.enqueue_inbox(event(), now=0.0)
    store.claim_inbox(worker_id="worker", now=1.0)
    assert store.fail_inbox("evt-1", "temporary", retry_at=10.0, now=2.0) is True
    assert store.claim_inbox(worker_id="worker", now=9.0) == []
    assert store.claim_inbox(worker_id="worker", now=10.0)[0].attempts == 2
    assert store.fail_inbox("evt-1", "permanent", now=11.0) is True
    assert store.claim_inbox(worker_id="worker", now=12.0) == []
    assert store.stats()["inbox_failed"] == 1
    store.close()


def test_outbox_is_idempotent_and_recoverable(tmp_path):
    store = DurableInboxOutbox(tmp_path / "queue.db")
    outbox_id = store.enqueue_outbox(
        event_id="evt-1",
        subject_id="eva-001",
        topic="chat.reply",
        payload={"text": "hello"},
        now=0.0,
    )
    assert outbox_id is not None
    assert store.enqueue_outbox(
        event_id="evt-1",
        subject_id="eva-001",
        topic="chat.reply",
        payload={"text": "duplicate"},
        now=1.0,
    ) is None

    claim = store.claim_outbox(worker_id="sender", lease_seconds=5.0, now=2.0)[0]
    assert claim.outbox_id == outbox_id
    assert claim.payload == {"text": "hello"}
    assert store.fail_outbox(outbox_id, "network", retry_at=10.0, now=3.0) is True
    assert store.claim_outbox(worker_id="sender", now=9.0) == []
    retry = store.claim_outbox(worker_id="sender", now=10.0)[0]
    assert retry.attempts == 2
    assert store.mark_outbox_sent(outbox_id, now=11.0) is True
    assert store.mark_outbox_sent(outbox_id, now=12.0) is True
    assert store.stats()["outbox_sent"] == 1
    store.close()


def test_invalid_outbox_payload_is_rejected_before_write(tmp_path):
    store = DurableInboxOutbox(tmp_path / "queue.db")
    try:
        store.enqueue_outbox(
            event_id="evt-1",
            subject_id="eva-001",
            topic="test",
            payload={"bad": float("nan")},
        )
    except ValueError as exc:
        assert "finite JSON" in str(exc)
    else:
        raise AssertionError("non-finite outbox payload was accepted")
    assert store.stats() == {}
    store.close()


def test_event_bus_adapter_uses_durable_admission_when_configured(tmp_path):
    durable = DurableInboxOutbox(tmp_path / "queue.db")
    adapter = EventBusAdapter(EventBus(), durable_queue=durable, subject_id="test-eva")
    evt = routable_event()

    import asyncio

    assert asyncio.run(adapter.publish(evt)) is True
    assert asyncio.run(adapter.publish(evt)) is False
    assert durable.stats()["inbox_pending"] == 1
    durable.close()
