from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from memory.memory_schema import MemoryRecord, MemoryStatus, MemoryType


class MemoryDecayPolicy:
    def apply(self, mem: MemoryRecord, now: datetime) -> MemoryRecord:
        age = now - mem.created_at
        inactive = now - (mem.last_accessed_at or mem.created_at)

        if mem.memory_type == MemoryType.WORKING:
            if age > timedelta(hours=12):
                mem.status = MemoryStatus.FORGOTTEN

        elif mem.memory_type == MemoryType.EPISODIC:
            if mem.salience < 0.35 and inactive > timedelta(days=14):
                mem.status = MemoryStatus.ARCHIVED
            if mem.salience < 0.20 and inactive > timedelta(days=45):
                mem.status = MemoryStatus.FORGOTTEN

        elif mem.memory_type in (MemoryType.SEMANTIC, MemoryType.PERSONA):
            mem.metadata["recency_score"] = max(
                0.3, float(mem.metadata.get("recency_score", 1.0)) * 0.98
            )

        mem.updated_at = now
        return mem


class MemoryCompactor:
    def group_for_compaction(
        self, memories: Iterable[MemoryRecord]
    ) -> dict[str, list[MemoryRecord]]:
        groups: dict[str, list[MemoryRecord]] = defaultdict(list)
        for mem in memories:
            if mem.memory_type != MemoryType.EPISODIC:
                continue
            if mem.status != MemoryStatus.ACTIVE:
                continue
            key = self._topic_key(mem)
            groups[key].append(mem)

        return {key: group for key, group in groups.items() if len(group) >= 3}

    def compact_group(self, topic: str, group: list[MemoryRecord]) -> MemoryRecord:
        joined = "\n".join(f"- {mem.content}" for mem in group[:10])
        summary = f"Topic[{topic}] recurring events:\n{joined}"

        now = datetime.now(timezone.utc)
        from uuid import uuid4

        return MemoryRecord(
            id=str(uuid4()),
            memory_type=MemoryType.SEMANTIC,
            content=summary,
            salience=max(mem.salience for mem in group),
            confidence=min(0.95, sum(mem.confidence for mem in group) / len(group) + 0.1),
            conflict_keys=[],
            created_at=now,
            updated_at=now,
            metadata={
                "source_memory_ids": [mem.id for mem in group],
                "topic": topic,
                "compacted_from_count": len(group),
            },
        )

    def _topic_key(self, mem: MemoryRecord) -> str:
        if mem.conflict_keys:
            return mem.conflict_keys[0]
        return mem.memory_type.value
