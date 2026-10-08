"""Memory Explorer API — browse and search the five-tier memory system.

Provides endpoints to:
- Browse entries per tier
- Search across tiers (hybrid: keyword + optional vector)
- Drill into individual memory entries

Note: /api/memory/tiers is served by routes_memory.py to avoid
route shadowing (registered first).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from memory.provenance import read_provenance

logger = logging.getLogger("eva.api.memory")

router = APIRouter()


# ── Models ──────────────────────────────────────────────────

class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    tiers: list[str] = Field(default=["S1", "S2", "S3"])
    limit: int = Field(default=20, ge=1, le=100)


# ── Browse entries ──────────────────────────────────────────

@router.get("/api/memory/entries/{tier}")
def get_memory_entries(
    tier: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Browse entries in a specific memory tier."""
    container = request.app.state.container
    entries: list[dict[str, Any]] = []
    total = 0

    if tier == "S1":
        tiered = getattr(container, "tiered_memory", None)
        if tiered:
            s1_raw = tiered.s1.list_all()
            total = len(s1_raw)
            for key, value in s1_raw[offset:offset + limit]:
                entries.append({
                    "id": key,
                    "content": str(value.get("content", ""))[:500],
                    "source": str(value.get("source", "")),
                    "importance": float(value.get("importance", 0.5)),
                    "ts": value.get("ts", 0),
                    "tier": "S1",
                    "provenance": read_provenance(value),
                })

    elif tier == "S2":
        tiered = getattr(container, "tiered_memory", None)
        if tiered:
            try:
                rows = tiered.s2.list_recent(limit=limit)
                total = len(rows)
                for row in rows:
                    entries.append({
                        "id": row.get("id", ""),
                        "content": str(row.get("content", ""))[:500],
                        "summary": str(row.get("summary", ""))[:200],
                        "source": str(row.get("source", "")),
                        "priority": row.get("priority", 2),
                        "tags": row.get("tags_json", "[]"),
                        "created_at": str(row.get("created_at", "")),
                        "expires_at": str(row.get("expires_at", "")),
                        "tier": "S2",
                        "provenance": read_provenance(row),
                    })
            except Exception:
                logger.debug("S2 browse failed", exc_info=True)

    elif tier == "S3":
        tiered = getattr(container, "tiered_memory", None)
        if tiered:
            try:
                rows = tiered.s3.search("", limit=limit)
                total = len(rows)
                for row in rows:
                    entries.append({
                        "id": row.get("id", ""),
                        "content": str(row.get("content", ""))[:500],
                        "category": str(row.get("category", "")),
                        "importance": float(row.get("importance", 0.5)),
                        "source_event_id": str(row.get("source_event_id", "")),
                        "created_at": str(row.get("created_at", "")),
                        "tier": "S3",
                        "provenance": read_provenance(row),
                    })
            except Exception:
                logger.debug("S3 browse failed", exc_info=True)

    elif tier == "S4":
        wm = getattr(container, "world_model", None)
        if wm:
            try:
                entities = wm.list_entities(limit=limit)
                total = len(entities)
                for e in entities:
                    entries.append({
                        "id": e.get("id", ""),
                        "type": e.get("type", ""),
                        "name": str(e.get("name", ""))[:200],
                        "properties": e.get("properties_json", "{}"),
                        "updated_at": str(e.get("updated_at", "")),
                        "tier": "S4",
                        "provenance": read_provenance(e),
                        "field_provenance": e.get("field_provenance", {}),
                    })
            except Exception:
                logger.debug("S4 browse failed", exc_info=True)

    elif tier == "S5":
        store = getattr(container, "store", None)
        if store:
            try:
                rows = store.fetchall(
                    "SELECT id, type, source, timestamp, payload, status FROM events "
                    "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
                total_row = store.fetchone("SELECT COUNT(*) as cnt FROM events")
                total = total_row["cnt"] if total_row else 0
                for row in rows:
                    entries.append({
                        "id": row["id"],
                        "type": row.get("type", ""),
                        "source": str(row.get("source", "")),
                        "timestamp": str(row.get("timestamp", "")),
                        "status": str(row.get("status", "")),
                        "payload": str(row.get("payload", ""))[:300],
                        "tier": "S5",
                    })
            except Exception:
                logger.debug("S5 browse failed", exc_info=True)

    return {"tier": tier, "entries": entries, "total": total, "limit": limit, "offset": offset}


# ── Search across tiers ─────────────────────────────────────

@router.post("/api/memory/search")
def search_memory(payload: MemorySearchRequest, request: Request) -> dict[str, Any]:
    """Search across specified memory tiers."""
    container = request.app.state.container
    query = payload.query
    results: dict[str, list[dict[str, Any]]] = {}
    total = 0

    tiered = getattr(container, "tiered_memory", None)

    for tier in payload.tiers:
        tier_results: list[dict[str, Any]] = []

        if tier == "S2" and tiered:
            try:
                rows = tiered.s2.search(query, limit=payload.limit)
                for row in rows:
                    tier_results.append({
                        "id": row.get("id", ""),
                        "content": str(row.get("content", ""))[:300],
                        "summary": str(row.get("summary", ""))[:200],
                        "source": str(row.get("source", "")),
                        "tier": "S2",
                    })
            except Exception:
                pass

        elif tier == "S3" and tiered:
            try:
                rows = tiered.s3.search(query, limit=payload.limit)
                for row in rows:
                    tier_results.append({
                        "id": row.get("id", ""),
                        "content": str(row.get("content", ""))[:300],
                        "category": str(row.get("category", "")),
                        "importance": float(row.get("importance", 0.5)),
                        "tier": "S3",
                    })
            except Exception:
                pass

        elif tier == "S1" and tiered:
            try:
                s1_all = tiered.s1.list_all()
                q_lower = query.lower()
                for key, value in s1_all:
                    content = str(value.get("content", ""))
                    if q_lower in content.lower():
                        tier_results.append({
                            "id": key,
                            "content": content[:300],
                            "source": str(value.get("source", "")),
                            "tier": "S1",
                        })
                        if len(tier_results) >= payload.limit:
                            break
            except Exception:
                pass

        elif tier == "S4":
            wm = getattr(container, "world_model", None)
            if wm:
                try:
                    q_lower = query.lower()
                    entities = wm.list_entities(limit=200)
                    for e in entities:
                        name = str(e.get("name", ""))
                        etype = str(e.get("type", ""))
                        if q_lower in name.lower() or q_lower in etype.lower():
                            tier_results.append({
                                "id": e.get("id", ""),
                                "type": etype,
                                "name": name[:200],
                                "tier": "S4",
                                "provenance": read_provenance(e),
                                "field_provenance": e.get("field_provenance", {}),
                            })
                            if len(tier_results) >= payload.limit:
                                break
                except Exception:
                    pass

        if tier_results:
            results[tier] = tier_results
            total += len(tier_results)

    return {"query": query, "results": results, "total": total}


# ── Single entry detail ─────────────────────────────────────

@router.get("/api/memory/entry/{tier}/{entry_id}")
def get_memory_entry(tier: str, entry_id: str, request: Request) -> dict[str, Any]:
    """Get full detail for a single memory entry."""
    container = request.app.state.container

    if tier == "S2":
        tiered = getattr(container, "tiered_memory", None)
        if tiered:
            row = tiered.s2.get(entry_id)
            if row:
                return {"found": True, "tier": tier, "entry": dict(row)}
    elif tier == "S3":
        tiered = getattr(container, "tiered_memory", None)
        if tiered:
            row = tiered.s3.get(entry_id)
            if row:
                return {"found": True, "tier": tier, "entry": dict(row)}
    elif tier == "S4":
        wm = getattr(container, "world_model", None)
        if wm:
            entity = wm.get_entity(entry_id)
            if entity:
                return {"found": True, "tier": tier, "entry": asdict(entity)}
    elif tier == "S5":
        store = getattr(container, "store", None)
        if store:
            row = store.fetchone(
                "SELECT id, type, source, timestamp, payload, status FROM events WHERE id = ?",
                (entry_id,),
            )
            if row:
                return {"found": True, "tier": tier, "entry": dict(row)}

    return {"found": False, "tier": tier, "entry_id": entry_id}
