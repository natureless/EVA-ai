from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from memory.importance_scorer import ImportanceFeatures, ImportanceScorer
from memory.memory_compactor import MemoryCompactor, MemoryDecayPolicy
from memory.memory_conflict_resolver import MemoryConflictResolver
from memory.memory_schema import MemoryRecord, MemoryStatus, MemoryType
from memory.storage_adapter import BaseStorageAdapter

if TYPE_CHECKING:
    from memory.tiered_store import TieredMemoryManager


class MemoryRepository:
    def __init__(self, store: BaseStorageAdapter) -> None:
        self.store = store

    def upsert(self, record: MemoryRecord) -> None:
        payload = self._to_payload(record)
        self.store.execute(
            """
            INSERT OR REPLACE INTO memory_items (
                id, memory_type, content, source_event_id,
                salience, confidence, ttl_seconds, embedding_ref, summary_ref,
                conflict_keys_json, created_at, updated_at, last_accessed_at,
                status, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["memory_type"],
                payload["content"],
                payload["source_event_id"],
                payload["salience"],
                payload["confidence"],
                payload["ttl_seconds"],
                payload["embedding_ref"],
                payload["summary_ref"],
                self.store.dumps_json(payload["conflict_keys"]),
                payload["created_at"],
                payload["updated_at"],
                payload["last_accessed_at"],
                payload["status"],
                self.store.dumps_json(payload["metadata"]),
            ),
        )

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        row = self.store.fetchone(
            "SELECT * FROM memory_items WHERE id = ?",
            (memory_id,),
        )
        if not row:
            return None
        return self._from_row(row)

    def list_active_memories(self, limit: int = 1000) -> list[MemoryRecord]:
        rows = self.store.fetchall(
            """
            SELECT * FROM memory_items
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._from_row(row) for row in rows]

    def list_by_type(
        self, memory_type: str, status: str = "active", limit: int = 1000
    ) -> list[MemoryRecord]:
        rows = self.store.fetchall(
            """
            SELECT * FROM memory_items
            WHERE memory_type = ? AND status = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (memory_type, status, limit),
        )
        return [self._from_row(row) for row in rows]

    def find_by_conflict_keys(self, keys: list[str]) -> list[MemoryRecord]:
        if not keys:
            return []
        clauses = " OR ".join(["conflict_keys_json LIKE ?"] * len(keys))
        params = [f"%{key}%" for key in keys]
        rows = self.store.fetchall(
            f"SELECT * FROM memory_items WHERE {clauses}",
            params,
        )
        return [self._from_row(row) for row in rows]

    def _to_payload(self, record: MemoryRecord) -> dict[str, Any]:
        data = asdict(record)
        data["memory_type"] = record.memory_type.value
        data["status"] = record.status.value
        data["created_at"] = record.created_at.isoformat()
        data["updated_at"] = record.updated_at.isoformat()
        data["last_accessed_at"] = (
            record.last_accessed_at.isoformat() if record.last_accessed_at else None
        )
        return data

    def _from_row(self, row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            source_event_id=row.get("source_event_id"),
            salience=float(row.get("salience", 0.0)),
            confidence=float(row.get("confidence", 0.5)),
            ttl_seconds=row.get("ttl_seconds"),
            embedding_ref=row.get("embedding_ref"),
            summary_ref=row.get("summary_ref"),
            conflict_keys=self.store.loads_json(row.get("conflict_keys_json") or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_accessed_at=(
                datetime.fromisoformat(row["last_accessed_at"])
                if row.get("last_accessed_at")
                else None
            ),
            status=MemoryStatus(row.get("status", "active")),
            metadata=self.store.loads_json(row.get("metadata_json") or "{}"),
        )


class MemoryGovernor:
    def __init__(
        self,
        repository: MemoryRepository,
        tiered_memory: TieredMemoryManager | None = None,
        llm: object | None = None,
    ) -> None:
        self.repository = repository
        self.tiered_memory = tiered_memory
        self.importance = ImportanceScorer()
        self.conflict_resolver = MemoryConflictResolver()
        self.compactor = MemoryCompactor(llm=llm)
        self.decay_policy = MemoryDecayPolicy()

    def ingest(
        self, record: MemoryRecord, features: ImportanceFeatures, *, sync_tiers: bool = True
    ) -> MemoryRecord:
        now = datetime.now(timezone.utc)
        record.salience = self.importance.score(features)
        if record.metadata.get("long_term_allowed") is True:
            record.salience = max(0.8, record.salience)
        record.updated_at = now

        conflicts = self.conflict_resolver.detect(
            record, self.repository.find_by_conflict_keys(record.conflict_keys)
        )
        record = self.conflict_resolver.resolve(record, conflicts)

        self.repository.upsert(record)

        # bridge to tiered memory — high-salience memories flow into S2/S3
        if sync_tiers and self.tiered_memory and record.salience >= 0.6 and record.status == MemoryStatus.ACTIVE:
            try:
                record.metadata["tier_ids"] = self.tiered_memory.ingest(
                    record.content,
                    importance=record.salience,
                    source=record.memory_type.value,
                    category=record.memory_type.value,
                    source_event_id=record.source_event_id or "",
                    origin=record.metadata.get("provenance"),
                    allow_long_term=record.metadata.get("long_term_allowed") is True,
                    sync_governor=False,
                )
            except Exception:
                pass

        return record

    def access(self, memory_id: str) -> Optional[MemoryRecord]:
        mem = self.repository.get(memory_id)
        if not mem:
            return None
        mem.last_accessed_at = datetime.now(timezone.utc)
        self.repository.upsert(mem)
        return mem

    def maintenance(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        changed = 0
        compacted = 0

        memories = self.repository.list_active_memories(limit=2000)
        for mem in memories:
            before_status = mem.status
            updated = self.decay_policy.apply(mem, now)
            if updated.status != before_status:
                changed += 1
            self.repository.upsert(updated)

        episodic = self.repository.list_by_type("episodic", status="active", limit=1000)
        groups = self.compactor.group_for_compaction(episodic)
        for topic, group in groups.items():
            compacted_record = self.compactor.compact_group(topic, group)
            self.repository.upsert(compacted_record)
            for item in group:
                item.status = MemoryStatus.COMPRESSED
                item.updated_at = now
                self.repository.upsert(item)
            compacted += 1

        return {
            "changed": changed,
            "compacted": compacted,
            "groups": list(groups.keys()),
        }
