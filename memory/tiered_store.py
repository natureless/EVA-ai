"""Five-tier memory architecture implementing the EVA-VM storage model.

S1 - Session Memory    (in-memory, 30min TTL, ~200 entries)
S2 - Working Memory    (SQLite, 72h TTL, ~500 entries)
S3 - Long-term Memory  (SQLite, permanent, ~10000 entries)
S4 - World Model       (SQLite, entities + edges)
S5 - Event/Trace Store (SQLite, existing events/traces/snapshots)

The TieredMemoryManager routes memories by importance:
  importance >= 0.8 → S1 + S2 + S3
  importance >= 0.6 → S1 + S2
  importance <  0.6 → S1 only
"""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from memory.sqlite_store import SQLiteStore

# ── FTS helpers ──────────────────────────────────────────────

def _fts_sanitize(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    Strips characters that FTS5 treats as operators (*, ", -, etc.)
    and wraps each token in quotes so they're searched literally.
    """
    cleaned = query.replace('"', "").replace("*", "").replace("-", "")
    tokens = cleaned.split()
    return " ".join(f'"{t}"' for t in tokens[:10]) if tokens else cleaned


# ── S1: Session Memory ─────────────────────────────────────

@dataclass
class SessionEntry:
    key: str
    value: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)


class SessionMemory:
    """In-memory session store with automatic TTL eviction."""

    def __init__(self, max_entries: int = 200, ttl_seconds: int = 1800) -> None:
        self._store: dict[str, SessionEntry] = {}
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._hit_count = 0
        self._miss_count = 0

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self.evict_expired()
            if len(self._store) >= self.max_entries:
                oldest = min(self._store.values(), key=lambda e: e.accessed_at)
                del self._store[oldest.key]
            self._store[key] = SessionEntry(key=key, value=value)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._miss_count += 1
                return None
            if (time.time() - entry.created_at) > self.ttl_seconds:
                del self._store[key]
                self._miss_count += 1
                return None
            entry.accessed_at = time.time()
            self._hit_count += 1
            return entry.value

    def evict_expired(self) -> int:
        now = time.time()
        expired = [
            k for k, e in self._store.items()
            if (now - e.created_at) > self.ttl_seconds
        ]
        for k in expired:
            del self._store[k]
        return len(expired)

    def stats(self) -> dict:
        with self._lock:
            return {
                "tier": "S1_session",
                "entries": len(self._store),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "hit_count": self._hit_count,
                "miss_count": self._miss_count,
            }

    def dump_to_s2(self, s2_store) -> int:
        """Flush all S1 entries to S2 working memory so context survives restart."""
        count = 0
        with self._lock:
            for entry in self._store.values():
                try:
                    s2_store.put(
                        str(entry.value.get("content", "")),
                        summary=str(entry.value.get("content", ""))[:200],
                        source=str(entry.value.get("source", "session")),
                        tags=["s1_dump"],
                    )
                    count += 1
                except Exception:
                    pass
        return count

    def restore_from_s2(self, s2_store) -> int:
        """Reload recently dumped S1 entries from S2 on startup."""
        count = 0
        try:
            for row in s2_store.list_recent(limit=200):
                content = row.get("content", "")
                if content:
                    mid = f"s1_{uuid4().hex[:8]}"
                    self.put(mid, {
                        "content": content,
                        "importance": 0.6,
                        "source": row.get("source", ""),
                        "ts": time.time(),
                    })
                    count += 1
        except Exception:
            pass
        return count


# ── S2: Working Memory ─────────────────────────────────────

class WorkingMemoryStore:
    """SQLite-backed working memory with configurable TTL."""

    def __init__(self, store: SQLiteStore, max_entries: int = 500, ttl_hours: int = 72) -> None:
        self.store = store
        self.max_entries = max_entries
        self.ttl_hours = ttl_hours

    def put(
        self,
        content: str,
        *,
        summary: str = "",
        source: str = "",
        priority: int = 2,
        tags: list[str] | None = None,
        memory_id: str | None = None,
    ) -> str:
        self.cleanup_expired()
        self._enforce_capacity()
        mid = memory_id or f"wm_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        expires = datetime.fromtimestamp(
            time.time() + self.ttl_hours * 3600, tz=timezone.utc
        ).isoformat()
        self.store.execute(
            """INSERT OR REPLACE INTO working_memory
               (id, content, summary, source, priority, tags_json, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, content, summary or content[:200], source, priority,
             json.dumps(tags or [], ensure_ascii=False), now, expires),
        )
        return mid

    def get(self, memory_id: str) -> dict | None:
        row = self.store.fetchone(
            "SELECT * FROM working_memory WHERE id = ?", (memory_id,)
        )
        if not row:
            return None
        row["tags"] = json.loads(row.get("tags_json", "[]"))
        return dict(row)

    def list_recent(self, limit: int = 50) -> list[dict]:
        rows = self.store.fetchall(
            """SELECT * FROM working_memory
               WHERE expires_at > ?
               ORDER BY created_at DESC LIMIT ?""",
            (datetime.now(timezone.utc).isoformat(), limit),
        )
        for r in rows:
            r["tags"] = json.loads(r.get("tags_json", "[]"))
        return [dict(r) for r in rows]

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute("DELETE FROM working_memory WHERE expires_at <= ?", (now,))
        return 0  # sqlite doesn't return rowcount easily with our wrapper

    def _enforce_capacity(self) -> None:
        rows = self.store.fetchall(
            "SELECT COUNT(*) as cnt FROM working_memory", ()
        )
        count = rows[0]["cnt"] if rows else 0
        if count > self.max_entries:
            self.store.execute(
                """DELETE FROM working_memory WHERE id IN
                   (SELECT id FROM working_memory ORDER BY created_at ASC
                    LIMIT ?)""",
                (count - self.max_entries,),
            )

    def stats(self) -> dict:
        rows = self.store.fetchall("SELECT COUNT(*) as cnt FROM working_memory", ())
        return {
            "tier": "S2_working",
            "entries": rows[0]["cnt"] if rows else 0,
            "max_entries": self.max_entries,
            "ttl_hours": self.ttl_hours,
        }


# ── S3: Long-term Memory ───────────────────────────────────

class LongTermMemoryStore:
    """SQLite-backed permanent memory with soft-delete."""

    def __init__(self, store: SQLiteStore, max_entries: int = 10000) -> None:
        self.store = store
        self.max_entries = max_entries

    def put(
        self,
        content: str,
        *,
        category: str = "general",
        importance: float = 0.5,
        source_event_id: str = "",
        embedding_ref: str = "",
        memory_id: str | None = None,
    ) -> str:
        self._enforce_capacity()
        mid = memory_id or f"ltm_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute(
            """INSERT OR REPLACE INTO long_term_memory
               (id, content, category, embedding_ref, importance, source_event_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (mid, content, category, embedding_ref, importance, source_event_id, now),
        )
        return mid

    def get(self, memory_id: str) -> dict | None:
        row = self.store.fetchone(
            "SELECT * FROM long_term_memory WHERE id = ? AND status = 'active'",
            (memory_id,),
        )
        return dict(row) if row else None

    def list_by_category(self, category: str, limit: int = 50) -> list[dict]:
        rows = self.store.fetchall(
            """SELECT * FROM long_term_memory
               WHERE category = ? AND status = 'active'
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (category, limit),
        )
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[dict]:
        rows = self.store.fetchall(
            """SELECT * FROM long_term_memory
               WHERE status = 'active'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search long-term memory using FTS5, with LIKE fallback.

        FTS5 handles tokenized search (fast, indexed). Falls back to
        substring LIKE when FTS5 virtual table is unavailable (e.g.,
        before migrations run).
        """
        try:
            rows = self.store.fetchall(
                """SELECT ltm.* FROM long_term_memory ltm
                   JOIN long_term_memory_fts fts ON ltm.rowid = fts.rowid
                   WHERE long_term_memory_fts MATCH ?
                   ORDER BY ltm.importance DESC, ltm.created_at DESC
                   LIMIT ?""",
                (_fts_sanitize(query), limit),
            )
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            pass

        # fallback: substring LIKE scan
        rows = self.store.fetchall(
            """SELECT * FROM long_term_memory
               WHERE status = 'active' AND content LIKE ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (f"%{query}%", limit),
        )
        return [dict(r) for r in rows]

    def archive(self, memory_id: str) -> None:
        self.store.execute(
            "UPDATE long_term_memory SET status = 'archived' WHERE id = ?",
            (memory_id,),
        )

    def _enforce_capacity(self) -> None:
        rows = self.store.fetchall("SELECT COUNT(*) as cnt FROM long_term_memory", ())
        count = rows[0]["cnt"] if rows else 0
        if count > self.max_entries * 0.9:
            self.store.execute(
                """UPDATE long_term_memory SET status = 'archived'
                   WHERE id IN (
                     SELECT id FROM long_term_memory
                     WHERE status = 'active'
                     ORDER BY importance ASC, created_at ASC
                     LIMIT ?
                   )""",
                (min(500, count - self.max_entries + 100),),
            )

    def stats(self) -> dict:
        active = self.store.fetchall(
            "SELECT COUNT(*) as cnt FROM long_term_memory WHERE status = 'active'", ()
        )
        archived = self.store.fetchall(
            "SELECT COUNT(*) as cnt FROM long_term_memory WHERE status = 'archived'", ()
        )
        return {
            "tier": "S3_long_term",
            "entries_active": active[0]["cnt"] if active else 0,
            "entries_archived": archived[0]["cnt"] if archived else 0,
            "max_entries": self.max_entries,
        }


# ── S4: World Model ────────────────────────────────────────

class WorldModelStore:
    """Structured world representation: entities + typed edges."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    # -- entities -------------------------------------------------------------

    def upsert_entity(
        self, entity_id: str, entity_type: str, name: str, properties: dict | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute(
            """INSERT OR REPLACE INTO world_entities
               (id, type, name, properties_json, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (entity_id, entity_type, name,
             json.dumps(properties or {}, ensure_ascii=False), now),
        )

    def get_entity(self, entity_id: str) -> dict | None:
        row = self.store.fetchone(
            "SELECT * FROM world_entities WHERE id = ?", (entity_id,)
        )
        if row:
            row["properties"] = json.loads(row.get("properties_json", "{}"))
        return dict(row) if row else None

    def list_entities(self, entity_type: str = "", limit: int = 100) -> list[dict]:
        if entity_type:
            rows = self.store.fetchall(
                "SELECT * FROM world_entities WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
                (entity_type, limit),
            )
        else:
            rows = self.store.fetchall(
                "SELECT * FROM world_entities ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        for r in rows:
            r["properties"] = json.loads(r.get("properties_json", "{}"))
        return [dict(r) for r in rows]

    # -- edges ----------------------------------------------------------------

    def upsert_edge(
        self, source: str, target: str, relation: str, weight: float = 1.0
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.execute(
            """INSERT OR REPLACE INTO world_edges
               (source, target, relation, weight, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source, target, relation, weight, now),
        )

    def list_edges(self, entity_id: str = "", limit: int = 200) -> list[dict]:
        if entity_id:
            rows = self.store.fetchall(
                "SELECT * FROM world_edges WHERE source = ? OR target = ? LIMIT ?",
                (entity_id, entity_id, limit),
            )
        else:
            rows = self.store.fetchall(
                "SELECT * FROM world_edges ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        entities = self.store.fetchall("SELECT COUNT(*) as cnt FROM world_entities", ())
        edges = self.store.fetchall("SELECT COUNT(*) as cnt FROM world_edges", ())
        return {
            "tier": "S4_world_model",
            "entities": entities[0]["cnt"] if entities else 0,
            "edges": edges[0]["cnt"] if edges else 0,
        }


# ── S5: Event / Trace / Snapshot ───────────────────────────

class EventTraceStore:
    """Read-only view over existing events, traces, and snapshots tables."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def count_events(self) -> int:
        rows = self.store.fetchall("SELECT COUNT(*) as cnt FROM events", ())
        return rows[0]["cnt"] if rows else 0

    def count_traces(self) -> int:
        rows = self.store.fetchall("SELECT COUNT(*) as cnt FROM traces", ())
        return rows[0]["cnt"] if rows else 0

    def stats(self) -> dict:
        return {
            "tier": "S5_event_trace",
            "events": self.count_events(),
            "traces": self.count_traces(),
        }


# ── Tiered Memory Manager ──────────────────────────────────

class TieredMemoryManager:
    """Unified interface to all five memory tiers.

    Usage::

        mgr = TieredMemoryManager(store)
        mgr.ingest("user said hello", importance=0.7, source="chat")
        results = mgr.recall("hello", tiers=[2, 3])
        mgr.maintenance()
    """

    def __init__(
        self,
        store: SQLiteStore,
        config: dict | None = None,
    ) -> None:
        cfg = config or {}

        s1_cfg = cfg.get("S1_session", {})
        self.s1 = SessionMemory(
            max_entries=s1_cfg.get("max_entries", 200),
            ttl_seconds=s1_cfg.get("ttl_minutes", 30) * 60,
        )

        s2_cfg = cfg.get("S2_working", {})
        self.s2 = WorkingMemoryStore(
            store,
            max_entries=s2_cfg.get("max_entries", 500),
            ttl_hours=s2_cfg.get("ttl_hours", 72),
        )

        s3_cfg = cfg.get("S3_long_term", {})
        self.s3 = LongTermMemoryStore(
            store,
            max_entries=s3_cfg.get("max_entries", 10000),
        )

        self.s4 = WorldModelStore(store)
        self.s5 = EventTraceStore(store)

    # ── ingest ──────────────────────────────────────────────

    def ingest(
        self,
        content: str,
        *,
        importance: float = 0.5,
        source: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        source_event_id: str = "",
    ) -> dict[str, str]:
        """Route content to tiers based on importance."""
        return self._ingest_one(content, importance=importance, source=source,
                                category=category, tags=tags, source_event_id=source_event_id)

    def batch_ingest(
        self,
        items: list[dict],
    ) -> list[dict[str, str]]:
        """Batch-ingest multiple items efficiently.

        Each item is a dict with keys: content, importance (optional, default 0.5),
        source, category, tags, source_event_id.

        Uses execute_many for S2/S3 writes in a single transaction,
        reducing per-item latency from ~10ms to <0.1ms.
        """
        results: list[dict[str, str]] = []
        s2_batch: list[tuple] = []
        s3_batch: list[tuple] = []

        for item in items:
            content = str(item.get("content", ""))
            importance = float(item.get("importance", 0.5))
            source = str(item.get("source", ""))
            category = str(item.get("category", "general"))
            tags = item.get("tags") or []
            source_event_id = str(item.get("source_event_id", ""))

            result: dict[str, str] = {}

            # S1: always
            mid_s1 = f"s1_{uuid4().hex[:8]}"
            self.s1.put(mid_s1, {
                "content": content, "importance": importance,
                "source": source, "ts": time.time(),
            })
            result["s1"] = mid_s1

            # S2: importance >= 0.6
            if importance >= 0.6:
                mid_s2 = f"wm_{uuid4().hex[:12]}"
                now = datetime.now(timezone.utc).isoformat()
                expires = datetime.fromtimestamp(
                    time.time() + self.s2.ttl_hours * 3600, tz=timezone.utc
                ).isoformat()
                s2_batch.append((
                    mid_s2, content, content[:200], source, 2,
                    json.dumps(tags, ensure_ascii=False), now, expires,
                ))
                result["s2"] = mid_s2

            # S3: importance >= 0.8
            if importance >= 0.8:
                mid_s3 = f"ltm_{uuid4().hex[:12]}"
                now = datetime.now(timezone.utc).isoformat()
                s3_batch.append((
                    mid_s3, content, category, "", importance,
                    source_event_id, "active", now,
                ))
                result["s3"] = mid_s3

            results.append(result)

        # batch-write S2
        if s2_batch:
            self.s2.store.execute_many(
                """INSERT OR REPLACE INTO working_memory
                   (id, content, summary, source, priority, tags_json, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                s2_batch,
            )

        # batch-write S3
        if s3_batch:
            self.s3.store.execute_many(
                """INSERT OR REPLACE INTO long_term_memory
                   (id, content, category, embedding_ref, importance, source_event_id, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                s3_batch,
            )

        return results

    def _ingest_one(
        self,
        content: str,
        *,
        importance: float = 0.5,
        source: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        source_event_id: str = "",
    ) -> dict[str, str]:
        """Single-item ingest (for cognition loop)."""
        result: dict[str, str] = {}

        # S1: always
        mid_s1 = f"s1_{uuid4().hex[:8]}"
        self.s1.put(mid_s1, {
            "content": content, "importance": importance,
            "source": source, "ts": time.time(),
        })
        result["s1"] = mid_s1

        # S2: importance >= 0.6
        if importance >= 0.6:
            mid_s2 = self.s2.put(
                content,
                summary=content[:200],
                source=source,
                tags=tags or [],
            )
            result["s2"] = mid_s2

        # S3: importance >= 0.8
        if importance >= 0.8:
            mid_s3 = self.s3.put(
                content,
                category=category,
                importance=importance,
                source_event_id=source_event_id,
            )
            result["s3"] = mid_s3

        return result

    # ── recall ──────────────────────────────────────────────

    def recall(self, query: str, tiers: list[int] | None = None) -> list[dict]:
        """Search across specified tiers (default: all).

        S1: in-memory substring scan (small, fast)
        S2: SQLite LIKE scan (limited by recent window)
        S3: FTS5 indexed search with LIKE fallback
        """
        tiers = tiers or [1, 2, 3]
        results: list[dict] = []
        q = query.lower()

        if 1 in tiers:
            for key, entry in self.s1._store.items():
                if q in str(entry.value.get("content", "")).lower():
                    results.append({"tier": "S1", "key": key, **entry.value})

        if 2 in tiers:
            for row in self.s2.list_recent(limit=100):
                if q in row.get("content", "").lower():
                    results.append({"tier": "S2", **row})

        if 3 in tiers:
            rows = self.s3.search(query, limit=100)
            for row in rows:
                results.append({"tier": "S3", **row})

        return results

    # ── promote ─────────────────────────────────────────────

    def promote_to_s3(self, s2_memory_id: str) -> str | None:
        """Promote a working memory to long-term."""
        row = self.s2.get(s2_memory_id)
        if not row:
            return None
        return self.s3.put(
            row["content"],
            category="promoted",
            importance=0.8,
            source_event_id=row.get("source", ""),
        )

    # ── maintenance ─────────────────────────────────────────

    def maintenance(self) -> dict:
        s1_evicted = self.s1.evict_expired()
        s2_cleaned = self.s2.cleanup_expired()
        return {
            "s1_evicted": s1_evicted,
            "s2_cleaned": s2_cleaned > 0,
        }

    # ── stats ───────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "S1_session": self.s1.stats(),
            "S2_working": self.s2.stats(),
            "S3_long_term": self.s3.stats(),
            "S4_world_model": self.s4.stats(),
            "S5_event_trace": self.s5.stats(),
        }
