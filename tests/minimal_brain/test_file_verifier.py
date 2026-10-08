"""Checks consume real bounded file reads, never caller-supplied observations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import os
import sqlite3
from threading import Event

import pytest

from packages.minimal_brain.business_goal_store import BusinessGoalStore
from packages.minimal_brain.business_goals import BusinessGoalConflict
from packages.minimal_brain.file_verifier import (
    FileVerificationSpec,
    WorkspaceFileVerifier,
)


def spec(path="result.txt", content=b"expected"):
    return {
        "kind": "workspace_files_sha256_v1",
        "files": [
            {"path": path, "sha256": hashlib.sha256(content).hexdigest()},
        ],
    }


def goal(store, verification=None, **kwargs):
    created = store.create(
        task_id="task",
        source_event_id="event",
        description="match expected artifact bytes",
        success_conditions=[{"field": "checks.files_match", "expected": True}],
        verification=verification or spec(),
        **kwargs,
    )
    store.record_receipt(
        {
            "task_id": "task",
            "event_id": "event",
            "terminal_state": "succeeded",
            "ok": True,
        }
    )
    return store.get(created["goal_id"])


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    verifier = WorkspaceFileVerifier(root)
    store = BusinessGoalStore(tmp_path / "eva.db", verifier=verifier)
    yield root, verifier, store
    store.close()


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/absolute",
        "C:/secret",
        "C:secret",
        "dir\\secret",
        "dir//secret",
        "./file",
        ".env",
        "dir/.git/file",
        "file:stream",
        "CON",
        "dir/NUL.txt",
        "file.",
        "file ",
        "dir/../file",
    ],
)
def test_invalid_paths_never_reach_filesystem(path):
    with pytest.raises(ValueError):
        FileVerificationSpec.model_validate(spec(path))


def test_manifest_rejects_empty_duplicate_and_unknown_specs():
    for invalid in [
        {"kind": "workspace_files_sha256_v1", "files": []},
        {"kind": "shell_command", "files": spec()["files"]},
        {**spec(), "observations": {"passed": True}},
        {**spec(), "files": spec()["files"] * 2},
    ]:
        with pytest.raises(ValueError):
            FileVerificationSpec.model_validate(invalid)


def test_real_file_match_persists_completion_and_provenance(setup, tmp_path):
    root, verifier, store = setup
    (root / "result.txt").write_bytes(b"expected")
    before = goal(store)
    done = store.verify(before["goal_id"], expected_version=before["version"])
    assert done["status"] == "completed" and done["verification_state"] == "passed"
    check = done["last_verification"]
    assert check["source_event_id"] == "event"
    assert check["base_goal_version"] == before["version"]
    assert check["committed_goal_version"] == done["version"]
    assert check["files"][0]["observed_sha256"] == spec()["files"][0]["sha256"]
    assert "expected" not in repr(check["files"]).replace("expected_sha256", "")
    store.close()
    reopened = BusinessGoalStore(tmp_path / "eva.db", verifier=verifier)
    try:
        assert reopened.recover() == 0
        assert reopened.get(done["goal_id"]) == done
    finally:
        reopened.close()


def test_mismatch_and_missing_remain_pending_then_can_be_checked_again(setup):
    root, _, store = setup
    before = goal(store)
    missing = store.verify(before["goal_id"], expected_version=before["version"])
    assert missing["status"] == "pending_verification"
    assert missing["last_verification"]["files"][0]["outcome"] == "missing"
    (root / "result.txt").write_bytes(b"incorrect")
    mismatch = store.verify(before["goal_id"], expected_version=missing["version"])
    assert (
        mismatch["status"] == "pending_verification"
        and mismatch["verification_state"] == "mismatch"
    )
    (root / "result.txt").write_bytes(b"expected")
    assert (
        store.verify(before["goal_id"], expected_version=mismatch["version"])["status"]
        == "completed"
    )
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM business_goal_verifications"
        ).fetchone()[0]
        == 3
    )


def test_size_limit_and_io_failures_are_unknown(setup, monkeypatch):
    root, verifier, store = setup
    (root / "result.txt").write_bytes(b"expected")
    verifier.max_file_bytes = 3
    before = goal(store)
    result = store.verify(before["goal_id"], expected_version=before["version"])
    assert (
        result["status"] == "pending_verification"
        and result["verification_state"] == "unknown"
    )
    assert result["last_verification"]["checks"] == {}
    verifier.max_file_bytes = 100

    def denied(*_):
        raise PermissionError("private absolute path must not be returned")

    monkeypatch.setattr(verifier, "_digest", denied)
    result = store.verify(before["goal_id"], expected_version=result["version"])
    assert result["last_verification"]["files"][0]["reason"] == "read_error"
    assert "private absolute" not in repr(result)


def test_changed_file_during_read_is_not_success(setup, monkeypatch):
    root, verifier, _ = setup
    target = root / "result.txt"
    target.write_bytes(b"expected")
    original = os.read
    changed = False

    def mutate(fd, length):
        nonlocal changed
        chunk = original(fd, length)
        if not changed:
            target.write_bytes(b"changed and longer")
            changed = True
        return chunk

    monkeypatch.setattr(os, "read", mutate)
    result = verifier.run(spec())
    assert result["outcome"] == "unknown"
    assert result["files"][0]["reason"] == "file_changed"


def test_symlink_is_not_read(setup, tmp_path):
    root, verifier, _ = setup
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"expected")
    try:
        (root / "result.txt").symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit creating symlinks")
    result = verifier.run(spec())
    assert result["outcome"] == "unknown"
    assert result["files"][0]["observed_sha256"] is None


def test_cancel_during_check_rejects_stale_result_without_holding_database_lock(
    setup, monkeypatch
):
    root, verifier, store = setup
    (root / "result.txt").write_bytes(b"expected")
    before = goal(store)
    entered, release = Event(), Event()
    original = verifier.run

    def delayed(value):
        entered.set()
        assert release.wait(5)
        return original(value)

    monkeypatch.setattr(verifier, "run", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            store.verify, before["goal_id"], expected_version=before["version"]
        )
        try:
            assert entered.wait(5)
            cancelled = store.cancel(
                before["goal_id"], expected_version=before["version"]
            )
        finally:
            release.set()
        with pytest.raises(BusinessGoalConflict, match="version"):
            future.result(timeout=5)
    assert store.get(before["goal_id"]) == cancelled
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM business_goal_verifications"
        ).fetchone()[0]
        == 0
    )


def test_deadline_during_check_cannot_commit_completion(setup, monkeypatch):
    root, verifier, store = setup
    (root / "result.txt").write_bytes(b"expected")
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    store._clock = lambda: now
    before = goal(store, deadline=now + timedelta(seconds=1))
    original = verifier.run

    def delayed(value):
        nonlocal now
        result = original(value)
        now += timedelta(seconds=1)
        return result

    monkeypatch.setattr(verifier, "run", delayed)
    with pytest.raises(BusinessGoalConflict, match="deadline"):
        store.verify(before["goal_id"], expected_version=before["version"])
    after = store.get(before["goal_id"])
    assert after["status"] == "expired" and after["last_verification"] is None


def test_verification_and_goal_state_rollback_together(setup):
    root, _, store = setup
    (root / "result.txt").write_bytes(b"expected")
    before = goal(store)
    store._conn.execute("""CREATE TRIGGER fail_verification BEFORE INSERT ON business_goal_verifications
                           BEGIN SELECT RAISE(ABORT, 'injected'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.verify(before["goal_id"], expected_version=before["version"])
    assert store.get(before["goal_id"]) == before


def test_recovery_allows_rechecking_successful_request_but_not_unknown_execution(setup):
    root, _, store = setup
    (root / "result.txt").write_bytes(b"expected")
    before = goal(store)
    store.recover()
    restored = store.get(before["goal_id"])
    assert restored["status"] == "interrupted"
    assert (
        store.verify(before["goal_id"], expected_version=restored["version"])["status"]
        == "completed"
    )
    other = store.create(
        task_id="unknown",
        source_event_id="unknown",
        description="unknown action",
        success_conditions=[{"field": "checks.files_match", "expected": True}],
        verification=spec(),
    )
    store.record_receipt(
        {
            "task_id": "unknown",
            "event_id": "unknown",
            "terminal_state": "outcome_unknown",
            "ok": False,
        }
    )
    other = store.get(other["goal_id"])
    with pytest.raises(BusinessGoalConflict, match="successful processing"):
        store.verify(other["goal_id"], expected_version=other["version"])
