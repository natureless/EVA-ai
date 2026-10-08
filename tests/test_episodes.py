"""EPI-01 episode trace and side-effect-free replay tests."""

import json

import pytest

from memory.episodes import (
    EpisodeAction,
    EpisodeRecord,
    EpisodeResult,
    EpisodeSchemaError,
    StateReference,
    read_episode,
    replay_projection,
)
from packages.kernel.episode_store import DuplicateEpisodeError, EpisodeStore


def make_episode(event_id: str = "evt-1") -> EpisodeRecord:
    return EpisodeRecord(
        event_id=event_id,
        event_type="perception.user_message_received",
        source="user",
        correlation_id="corr-1",
        state_before=StateReference(version=4, integrity_hash="before"),
        state_after=StateReference(version=5, integrity_hash="after"),
        policy_version="policy-v2",
        strategy_version="strategy-v3",
        actions=[EpisodeAction(kind="chat", status="completed", receipt_id="receipt-1")],
        result=EpisodeResult(
            status="succeeded",
            ok=True,
            summary="reply delivered",
            receipt_id="receipt-1",
            observed={"reply_length": 12},
        ),
    )


def test_episode_serialization_preserves_trace():
    episode = make_episode()
    restored = read_episode(json.loads(json.dumps(episode.as_record())))

    assert restored.event_id == "evt-1"
    assert restored.state_before.version == 4
    assert restored.state_after.integrity_hash == "after"
    assert restored.actions[0].receipt_id == "receipt-1"
    assert restored.result.ok is True


def test_replay_projection_never_replays_external_actions():
    projection = replay_projection(make_episode())

    assert projection["state_before"]["version"] == 4
    assert projection["action_ids"]
    assert projection["replay_effect"] == "rebuild_state_only"
    assert projection["external_actions_replayed"] is False


def test_episode_store_is_append_only_and_idempotent(tmp_path):
    db = tmp_path / "episodes.db"
    store = EpisodeStore(db)
    episode = make_episode()
    assert store.append(episode) is True
    assert store.append(episode) is False
    assert store.get_by_event("evt-1").episode_id == episode.episode_id
    assert store.replay(episode.episode_id)["external_actions_replayed"] is False
    store.close()

    reopened = EpisodeStore(db)
    assert reopened.stats() == {"episodes": 1, "subjects": 1}
    assert len(reopened.list_subject("eva-001")) == 1
    reopened.close()


def test_episode_store_rejects_conflicting_event_or_episode_id(tmp_path):
    store = EpisodeStore(tmp_path / "episodes.db")
    original = make_episode()
    store.append(original)
    conflicting = make_episode("evt-2").model_copy(update={"episode_id": original.episode_id})
    with pytest.raises(DuplicateEpisodeError):
        store.append(conflicting)
    store.close()


def test_episode_schema_and_state_order_are_strict():
    episode = make_episode()
    with pytest.raises(ValueError, match="older"):
        EpisodeRecord.model_validate({
            **episode.model_dump(),
            "state_after": {"version": 3},
        })

    with pytest.raises(EpisodeSchemaError):
        read_episode({**episode.as_record(), "schema_version": 99})
