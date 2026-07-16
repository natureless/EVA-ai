from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4
from pathlib import Path

from memory.importance_scorer import ImportanceFeatures, ImportanceScorer
from memory.memory_conflict_resolver import MemoryConflictResolver
from memory.memory_compactor import MemoryDecayPolicy
from memory.memory_governor import MemoryGovernor, MemoryRepository
from memory.memory_schema import MemoryRecord, MemoryType
from memory.sqlite_store import SQLiteStore


def build_repo():
    tmp = TemporaryDirectory(ignore_cleanup_errors=True)
    store = SQLiteStore(Path(tmp.name) / "test.db")
    store.init_db()
    repo = MemoryRepository(store)
    return tmp, repo


def test_importance_score_higher_for_goal_related():
    scorer = ImportanceScorer()
    low = scorer.score(ImportanceFeatures())
    high = scorer.score(ImportanceFeatures(user_explicit=True, goal_related=True))
    assert high > low


def test_conflict_detector_finds_same_key_different_content():
    detector = MemoryConflictResolver()

    old = MemoryRecord(
        id=str(uuid4()),
        memory_type=MemoryType.PERSONA,
        content="User likes frequent social activity",
        conflict_keys=["social_preference"],
    )
    new = MemoryRecord(
        id=str(uuid4()),
        memory_type=MemoryType.PERSONA,
        content="User dislikes frequent social activity",
        conflict_keys=["social_preference"],
    )

    conflicts = detector.detect(new, [old])
    assert len(conflicts) == 1


def test_decay_forgets_old_working_memory():
    policy = MemoryDecayPolicy()
    mem = MemoryRecord(
        id=str(uuid4()),
        memory_type=MemoryType.WORKING,
        content="Temporary task context",
        created_at=datetime.now(timezone.utc) - timedelta(hours=13),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=13),
    )

    updated = policy.apply(mem, datetime.now(timezone.utc))
    assert updated.status.value == "forgotten"


def test_governor_marks_conflict():
    tmp, repo = build_repo()
    try:
        governor = MemoryGovernor(repo)
        existing = MemoryRecord(
            id=str(uuid4()),
            memory_type=MemoryType.PERSONA,
            content="User prefers concise responses",
            conflict_keys=["verbosity"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        repo.upsert(existing)

        candidate = MemoryRecord(
            id=str(uuid4()),
            memory_type=MemoryType.PERSONA,
            content="User prefers detailed responses",
            conflict_keys=["verbosity"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        result = governor.ingest(candidate, ImportanceFeatures())
        assert result.status.value == "conflicted"
    finally:
        tmp.cleanup()
