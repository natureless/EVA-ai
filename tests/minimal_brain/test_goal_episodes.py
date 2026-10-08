"""Optional Episode references are bounded, truthful and strictly read-only."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from memory.episodes import EpisodeAction, EpisodeRecord, EpisodeResult, StateReference
from packages.kernel.episode_store import EpisodeStore
from packages.minimal_brain.business_goal_store import BusinessGoalStore


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "eva.db"
    goals = BusinessGoalStore(path)
    episodes = EpisodeStore(path)
    try:
        yield goals, episodes
    finally:
        episodes.close()
        goals.close()


def goal(store, task="task", event="event", **kwargs):
    return store.create(
        task_id=task,
        source_event_id=event,
        description="追溯执行",
        success_conditions=[{"field": "checks.ready", "expected": True}],
        **kwargs,
    )


def episode(**kwargs):
    return EpisodeRecord(
        event_id="event",
        event_type="user_message",
        source="chat",
        state_before=StateReference(version=5, integrity_hash="before"),
        state_after=StateReference(version=6, integrity_hash="after"),
        result=EpisodeResult(
            status="succeeded",
            summary="private model text",
            observed={"secret": "private"},
        ),
        metadata={"private": "not projected"},
        **kwargs,
    )


def test_real_reference_survives_reopen_and_does_not_complete_goal(
    stores, tmp_path, monkeypatch
):
    goals, episodes = stores
    g, e = (
        goal(goals),
        episode(
            actions=[
                EpisodeAction(kind="chat", status="completed", receipt_id="receipt")
            ]
        ),
    )
    episodes.append(e)
    before = goals._conn.total_changes
    for name in ("get", "record_receipt", "verify", "recover"):
        monkeypatch.setattr(
            goals,
            name,
            lambda *a, **kw: pytest.fail("reference must not mutate or execute"),
        )
    reference = goals.episode_reference(g["goal_id"])
    assert reference["status"] == "available" and reference["read_only"] is True
    projection = reference["episode"]
    assert projection["event_id"] == g["source_event_id"]
    assert projection["episode_id"] == e.episode_id
    assert projection["state_before"]["version"] == 5
    assert projection["external_actions_replayed"] is False
    assert projection["actions"][0]["receipt_id"] == "receipt"
    assert "private" not in json.dumps(projection)
    graph = goals.graph()
    gnode, enode = graph["nodes"]
    assert gnode["status"] == "active" and gnode["record"]["version"] == g["version"]
    assert enode["record"] == projection and enode["tier"] == "E"
    assert enode["status"] == "episode_succeeded" and enode["source"] == "episode_store"
    assert graph["edges"] == [
        {
            "source": gnode["id"],
            "target": enode["id"],
            "kind": "goal_evidence",
            "relation": "source_event_episode",
        }
    ]
    assert goals._conn.total_changes == before
    reopened = BusinessGoalStore(tmp_path / "eva.db")
    try:
        assert reopened.episode_reference(g["goal_id"]) == reference
    finally:
        reopened.close()


def test_missing_store_and_event_do_not_create_tables_or_nodes(tmp_path):
    goals = BusinessGoalStore(tmp_path / "eva.db")
    try:
        g = goal(goals)
        before = goals._conn.total_changes
        ref = goals.episode_reference(g["goal_id"])
        assert ref["status"] == "not_recorded" and ref["reason"] == "store_missing"
        assert goals.graph()["edges"] == []
        assert not goals._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='episodes'"
        ).fetchone()
        episodes = EpisodeStore(tmp_path / "eva.db")
        try:
            episodes.append(
                EpisodeRecord(
                    event_id="different",
                    event_type="chat",
                    source="test",
                    state_before=StateReference(version=0),
                )
            )
            ref = goals.episode_reference(g["goal_id"])
            assert ref["reason"] == "event_missing" and ref["episode"] is None
            assert (
                len(goals.graph()["nodes"]) == 1 and goals._conn.total_changes == before
            )
        finally:
            episodes.close()
    finally:
        goals.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"event_id": "spoof"}, "identity_mismatch"),
        ({"episode_id": "spoof"}, "identity_mismatch"),
        ({"subject_id": "spoof"}, "identity_mismatch"),
        ({"policy_version": "\ud800"}, "invalid_record"),
        ({"schema_version": 99}, "invalid_record"),
        ({"schema_version": True}, "invalid_record"),
        ({"state_after": {"version": 4}}, "invalid_record"),
    ],
)
def test_invalid_identity_or_schema_is_unavailable_and_never_linked(
    stores, change, reason
):
    goals, episodes = stores
    g, e = goal(goals), episode()
    episodes.append(e)
    episodes._conn.execute(
        "UPDATE episodes SET record_json=?", (json.dumps({**e.as_record(), **change}),)
    )
    episodes._conn.commit()
    reference = goals.episode_reference(g["goal_id"])
    assert reference["status"] == "unavailable" and reference["reason"] == reason
    assert reference["episode"] is None
    graph = goals.graph()
    assert graph["edges"] == [] and len(graph["nodes"]) == 1
    assert graph["scope"]["episode_references_unavailable"] == 1


@pytest.mark.parametrize(
    "encoded",
    ["broken", "[]", '{"schema_version":1}', '"' + "x" * 65537 + '"'],
    ids=["invalid-json", "array", "incomplete", "oversized"],
)
def test_malformed_and_oversized_rows_preserve_goal_view(stores, encoded):
    goals, episodes = stores
    g = goal(goals)
    episodes.append(episode())
    episodes._conn.execute("UPDATE episodes SET record_json=?", (encoded,))
    episodes._conn.commit()
    ref = goals.episode_reference(g["goal_id"])
    assert ref["status"] == "unavailable" and ref["episode"] is None
    assert not goals._conn.in_transaction
    assert len(goals.graph()["nodes"]) == 1


def test_action_projection_and_long_fields_are_bounded(stores):
    goals, episodes = stores
    g = goal(goals)
    episodes.append(
        episode(
            policy_version="p" * 200,
            strategy_version="s" * 200,
            actions=[
                EpisodeAction(kind="chat", receipt_id="r" * 300) for _ in range(20)
            ],
        )
    )
    ref = goals.episode_reference(g["goal_id"])["episode"]
    assert ref["action_count"] == 20 and len(ref["actions"]) == 16
    assert ref["actions_truncated"] and ref["fields_truncated"]
    assert (
        len(ref["policy_version"]) == 128
        and len(ref["actions"][0]["receipt_id"]) == 256
    )


@pytest.mark.parametrize("kind", [[], {}, 1, None, "private-unrecognized-kind"])
def test_unrecognized_completion_metadata_does_not_break_reference(stores, kind):
    goals, episodes = stores
    g = goal(goals)
    e = episode().model_copy(update={"metadata": {"completion_kind": kind}})
    episodes.append(e)
    before = goals._conn.total_changes
    reference = goals.episode_reference(g["goal_id"])
    assert reference["status"] == "available"
    assert reference["episode"]["completion_kind"] is None
    assert goals.graph()["counts"]["E"]["total"] == 1
    assert goals._conn.total_changes == before


def test_input_limit_counts_utf8_bytes_and_closed_store_rejects_reads(stores):
    goals, episodes = stores
    g = goal(goals)
    oversized = episode().model_copy(update={"metadata": {"text": "界" * 23000}})
    episodes.append(oversized)
    assert goals.episode_reference(g["goal_id"])["reason"] == "oversized_or_invalid"
    other = BusinessGoalStore(episodes._db_path)
    other.close()
    with pytest.raises(RuntimeError):
        other.episode_reference(g["goal_id"])


def test_same_event_shares_one_episode_node_and_two_real_references(stores):
    goals, episodes = stores
    goal(goals, task="one")
    goal(goals, task="two")
    episodes.append(episode())
    graph = goals.graph()
    assert graph["counts"]["E"]["total"] == 1 and len(graph["edges"]) == 2
    assert len({n["id"] for n in graph["nodes"]}) == len(graph["nodes"]) == 3


def test_reference_reads_do_not_advance_expiry_or_recover(stores):
    goals, episodes = stores
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    goals._clock = lambda: now
    g = goal(goals, deadline=now + timedelta(seconds=1))
    episodes.append(episode())
    now += timedelta(seconds=2)
    before = goals._conn.total_changes
    queries = []
    goals._conn.set_trace_callback(queries.append)
    ref = goals.episode_reference(g["goal_id"])
    graph = goals.graph()
    assert (
        ref["current_status"] == "active"
        and ref["current_goal_version"] == g["version"]
    )
    assert (
        graph["nodes"][0]["status"] == "active" and goals._conn.total_changes == before
    )
    assert not any(
        q.startswith(("UPDATE", "INSERT", "DELETE", "BEGIN IMMEDIATE")) for q in queries
    )


def test_optional_read_failure_is_distinct_from_absence_and_recovers(stores):
    goals, episodes = stores
    g = goal(goals)
    episodes.append(episode())

    def authorize(op, arg1, arg2, db, source):
        return (
            sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_READ and arg1 == "episodes"
            else sqlite3.SQLITE_OK
        )

    goals._conn.set_authorizer(authorize)
    assert goals.episode_reference(g["goal_id"])["reason"] == "read_failed"
    assert goals.graph()["edges"] == [] and not goals._conn.in_transaction
    goals._conn.set_authorizer(None)
    assert goals.episode_reference(g["goal_id"])["status"] == "available"
    with pytest.raises(KeyError):
        goals.episode_reference("missing")
    assert not goals._conn.in_transaction


def test_ambiguous_or_unsupported_table_is_never_used(tmp_path):
    goals = BusinessGoalStore(tmp_path / "eva.db")
    try:
        g = goal(goals)
        goals._conn.execute(
            "CREATE TABLE episodes (episode_id,event_id,subject_id,record_json)"
        )
        e = episode()
        for _ in range(2):
            goals._conn.execute(
                "INSERT INTO episodes VALUES (?,?,?,?)",
                (e.episode_id, e.event_id, e.subject_id, json.dumps(e.as_record())),
            )
        goals._conn.commit()
        assert goals.episode_reference(g["goal_id"])["reason"] == "ambiguous_event"
        goals._conn.execute("DROP TABLE episodes")
        goals._conn.execute("CREATE TABLE episodes (event_id)")
        goals._conn.commit()
        assert goals.episode_reference(g["goal_id"])["reason"] == "read_failed"
        assert goals.graph()["edges"] == []
    finally:
        goals.close()
