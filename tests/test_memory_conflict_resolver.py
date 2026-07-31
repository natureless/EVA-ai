"""Unit tests for MemoryConflictResolver — conflict detection and resolution."""

import pytest

from memory.memory_conflict_resolver import MemoryConflictResolver
from memory.memory_schema import MemoryRecord, MemoryStatus, MemoryType


def _make_record(content: str, conflict_keys: list | None = None,
                 status: MemoryStatus = MemoryStatus.ACTIVE) -> MemoryRecord:
    return MemoryRecord(
        id=f"mem_{content[:8]}",
        memory_type=MemoryType.EPISODIC,
        content=content,
        salience=0.5,
        confidence=0.7,
        conflict_keys=conflict_keys or [],
        status=status,
    )


class TestMemoryConflictResolver:
    def test_detect_no_conflict_keys_returns_empty(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("new info")
        existing = [_make_record("old info", conflict_keys=["topic_x"])]
        conflicts = resolver.detect(candidate, existing)
        assert conflicts == []

    def test_detect_no_overlap_returns_empty(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("new info", conflict_keys=["topic_a"])
        existing = [_make_record("old info", conflict_keys=["topic_b"])]
        conflicts = resolver.detect(candidate, existing)
        assert conflicts == []

    def test_detect_same_content_not_conflict(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("login bug fixed", conflict_keys=["login"])
        existing = [_make_record("login bug fixed", conflict_keys=["login"])]
        conflicts = resolver.detect(candidate, existing)
        assert conflicts == []

    def test_detect_different_content_is_conflict(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("login bug is fixed", conflict_keys=["login"])
        existing = [_make_record("login bug still broken", conflict_keys=["login"])]
        conflicts = resolver.detect(candidate, existing)
        assert len(conflicts) == 1
        assert conflicts[0].content == "login bug still broken"

    def test_detect_skips_forgotten(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("login bug fixed", conflict_keys=["login"])
        existing = [
            _make_record("login bug still broken", conflict_keys=["login"],
                         status=MemoryStatus.FORGOTTEN),
        ]
        conflicts = resolver.detect(candidate, existing)
        assert conflicts == []

    def test_detect_skips_archived(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("login bug fixed", conflict_keys=["login"])
        existing = [
            _make_record("login bug still broken", conflict_keys=["login"],
                         status=MemoryStatus.ARCHIVED),
        ]
        conflicts = resolver.detect(candidate, existing)
        assert conflicts == []

    def test_resolve_with_conflicts_marks_conflicted(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("new info", conflict_keys=["topic"])
        conflicts = [_make_record("old info", conflict_keys=["topic"])]
        result = resolver.resolve(candidate, conflicts)
        assert result.status == MemoryStatus.CONFLICTED
        assert "conflicting_memory_ids" in result.metadata

    def test_resolve_no_conflicts_keeps_active(self):
        resolver = MemoryConflictResolver()
        candidate = _make_record("new info", conflict_keys=["topic"])
        result = resolver.resolve(candidate, [])
        assert result.status == MemoryStatus.ACTIVE

    def test_normalized_whitespace(self):
        resolver = MemoryConflictResolver()
        assert resolver._normalized("  hello   world  ") == "hello world"

    def test_normalized_case_insensitive(self):
        resolver = MemoryConflictResolver()
        assert resolver._normalized("Hello World") == "hello world"
