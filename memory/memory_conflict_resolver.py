from __future__ import annotations

from typing import Iterable

from memory.memory_schema import MemoryRecord, MemoryStatus


class MemoryConflictResolver:
    def detect(self, candidate: MemoryRecord, existing: Iterable[MemoryRecord]) -> list[MemoryRecord]:
        conflicts: list[MemoryRecord] = []
        candidate_keys = set(candidate.conflict_keys)
        if not candidate_keys:
            return conflicts

        for mem in existing:
            if mem.status in {MemoryStatus.FORGOTTEN, MemoryStatus.ARCHIVED}:
                continue
            if not candidate_keys.intersection(set(mem.conflict_keys)):
                continue
            if self._normalized(candidate.content) != self._normalized(mem.content):
                conflicts.append(mem)

        return conflicts

    def resolve(
        self, candidate: MemoryRecord, conflicts: list[MemoryRecord]
    ) -> MemoryRecord:
        if conflicts:
            candidate.status = MemoryStatus.CONFLICTED
            candidate.metadata["conflicting_memory_ids"] = [mem.id for mem in conflicts]
        return candidate

    def _normalized(self, text: str) -> str:
        return " ".join(text.strip().lower().split())
