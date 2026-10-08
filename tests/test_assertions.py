"""EVD-02 temporal assertion and lifecycle invariants."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from memory.assertions import (
    AssertionSchemaError,
    AssertionStatus,
    TemporalAssertion,
    current_assertions,
    make_assertion,
    read_assertion,
)
from memory.provenance import EpistemicStatus


UTC = timezone.utc


def test_temporal_window_separates_observation_from_current_validity():
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    assertion = make_assertion(
        subject="project-a",
        predicate="status",
        value="active",
        source="tool",
        source_event_id="evt-1",
        observed_at=observed,
        valid_from=observed + timedelta(days=1),
        valid_until=observed + timedelta(days=3),
        epistemic_status=EpistemicStatus.TOOL_OBSERVATION,
    )

    assert assertion.is_current(observed) is False
    assert assertion.is_current(observed + timedelta(days=1)) is True
    assert assertion.is_current(observed + timedelta(days=3)) is False
    assert assertion.provenance["epistemic_status"] == "tool_observation"


def test_retraction_and_supersession_preserve_history():
    assertion = TemporalAssertion(subject="x", predicate="owner", value="Alice")
    retracted = assertion.retract(by_event_id="evt-retract", reason="source corrected")
    superseded = assertion.supersede(
        replacement_id="asrt-new",
        by_event_id="evt-update",
        reason="new observation",
    )

    assert assertion.status is AssertionStatus.ACTIVE
    assert retracted.status is AssertionStatus.RETRACTED
    assert retracted.retracted_by == "evt-retract"
    assert superseded.status is AssertionStatus.SUPERSEDED
    assert superseded.superseded_by == "asrt-new"
    assert not retracted.is_current() and not superseded.is_current()


def test_current_filter_does_not_merge_conflicting_values():
    first = make_assertion(subject="x", predicate="status", value="active", source="user")
    second = make_assertion(subject="x", predicate="status", value="blocked", source="tool")
    retracted = first.retract(by_event_id="evt", reason="correction")

    current = current_assertions([retracted, second])
    assert current == [second]
    assert current[0].value == "blocked"


def test_round_trip_is_json_safe_and_unsupported_schema_is_unknown():
    assertion = make_assertion(subject="x", predicate="priority", value={"level": 2}, source="user")
    record = json.loads(json.dumps(assertion.as_record(), ensure_ascii=False))
    restored = read_assertion(record)
    assert restored.assertion_id == assertion.assertion_id
    assert restored.value == {"level": 2}

    old = {**record, "schema_version": 99, "status": "active"}
    unknown = read_assertion(old)
    assert unknown.status is AssertionStatus.UNKNOWN
    assert not unknown.is_current()


def test_invalid_temporal_bounds_and_malformed_records_are_rejected():
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="valid_until"):
        TemporalAssertion(
            subject="x",
            predicate="status",
            value="active",
            valid_from=now,
            valid_until=now,
        )

    with pytest.raises(AssertionSchemaError):
        read_assertion({"schema_version": 1, "subject": "x"})
