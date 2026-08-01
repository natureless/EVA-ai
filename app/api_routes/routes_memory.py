from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/memory/recent")
def recent_memory(request: Request, limit: int = 20) -> dict[str, Any]:
    return {"items": request.app.state.container.memory_api.get_recent_memories(limit=limit)}


@router.get("/api/debug/trace")
def recent_trace(request: Request, limit: int = 20) -> dict[str, Any]:
    """最近执行追踪记录。"""
    return {"items": request.app.state.container.memory_api.get_recent_traces(limit=limit)}


@router.get("/api/events/stats")
def event_type_stats(request: Request) -> dict[str, Any]:
    """事件类型分布统计。"""
    container = request.app.state.container

    # From legacy EventBus
    bus_stats = container.event_bus.stats()

    # From MVSC EventStore (if enabled)
    mvsc_stats = {}
    if container.mvsc_components:
        event_store = container.mvsc_components.get("event_store")
        if event_store:
            try:
                mvsc_stats = event_store.stats()
            except Exception:
                pass

    return {
        "legacy_bus": bus_stats,
        "mvsc_event_store": mvsc_stats,
        "pending_events": container.event_bus.size(),
        "events_dropped": container.event_bus.dropped,
    }


@router.get("/api/events/recent")
def recent_events(request: Request, limit: int = 20) -> dict[str, Any]:
    return {"items": request.app.state.container.memory_api.get_recent_events(limit=limit)}


# ── Tiered Memory Endpoints ─────────────────────────────────

@router.get("/api/memory/tiers")
def memory_tiers(request: Request, format: str = "raw") -> dict[str, Any]:
    """Statistics for all five memory tiers.

    Args:
        format: \"raw\" (default, backward-compat for settings page) or
                \"explorer\" (transformed for Memory Explorer UI).
    """
    tm = request.app.state.container.tiered_memory
    if tm is None:
        if format == "explorer":
            return {"tiers": [], "total_entries": 0}
        return {"error": "tiered memory not available"}

    stats = tm.stats()

    if format == "explorer":
        tiers: list[dict[str, Any]] = []

        s1 = stats.get("S1_session", {})
        tiers.append({
            "name": "S1", "label": "Session",
            "entries": s1.get("entries", 0),
            "max": s1.get("max_entries", 200),
            "ttl": "30 min", "storage": "in-memory",
            "hits": s1.get("hit_count", 0),
            "misses": s1.get("miss_count", 0),
        })

        s2 = stats.get("S2_working", {})
        tiers.append({
            "name": "S2", "label": "Working",
            "entries": s2.get("entries", 0),
            "max": s2.get("max_entries", 500),
            "ttl": "72 hours", "storage": "SQLite",
            "expired": s2.get("expired_count", 0),
        })

        s3 = stats.get("S3_long_term", {})
        tiers.append({
            "name": "S3", "label": "Long-term",
            "entries": s3.get("entries_active", 0),
            "max": s3.get("max_entries", 10000),
            "ttl": "permanent", "storage": "SQLite + FTS5",
            "avg_importance": s3.get("avg_importance", 0),
        })

        s4 = stats.get("S4_world_model", {})
        tiers.append({
            "name": "S4", "label": "World Model",
            "entries": s4.get("entities", 0),
            "max": None, "ttl": "permanent", "storage": "SQLite",
            "edges": s4.get("edges", 0),
        })

        s5 = stats.get("S5_event_trace", {})
        tiers.append({
            "name": "S5", "label": "Event/Trace",
            "entries": s5.get("events", 0) + s5.get("traces", 0),
            "max": None, "ttl": "permanent", "storage": "SQLite",
            "events": s5.get("events", 0),
            "traces": s5.get("traces", 0),
        })

        total = sum(t["entries"] for t in tiers)
        return {"tiers": tiers, "total_entries": total}

    # Default: raw format (backward-compatible)
    return stats  # type: ignore[no-any-return]


@router.get("/api/memory/working")
def working_memory(request: Request, limit: int = 50) -> dict[str, Any]:
    """S1+S2 working memory — current session context."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"items": [], "s1": [], "s2": []}

    s1_entries = tm.s1.list_all()[:limit]
    s2_entries = tm.s2.list_recent(limit=limit)

    return {
        "s1": [
            {"key": k, "content": str(v.get("content", ""))[:200], "importance": v.get("importance", 0)}
            for k, v in s1_entries
        ],
        "s2": [
            {"id": r.get("id", ""), "content": r.get("content", "")[:200], "source": r.get("source", "")}
            for r in s2_entries
        ],
        "total": len(s1_entries) + len(s2_entries),
    }


@router.get("/api/memory/working/stats")
def working_memory_stats(request: Request) -> dict[str, Any]:
    """S1+S2 capacity and hit/miss statistics."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"status": "unavailable"}

    s1_stats = tm.s1.stats()
    s2_stats = tm.s2.stats()

    hits = s1_stats.get("hit_count", 0)
    misses = s1_stats.get("miss_count", 0)
    return {
        "s1": {
            "entries": s1_stats.get("entries", 0),
            "max": s1_stats.get("max_entries", 200),
            "hit_rate": round(hits / max(1, hits + misses), 3),
        },
        "s2": {
            "entries": s2_stats.get("entries", 0),
            "max": s2_stats.get("max_entries", 500),
        },
    }


@router.get("/api/memory/longterm")
def longterm_memory(request: Request, limit: int = 50, category: str = "") -> dict[str, Any]:
    """S3 long-term memory entries."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"items": []}
    if category:
        return {"items": tm.s3.list_by_category(category, limit=limit)}
    return {"items": tm.s3.list_recent(limit=limit)}


@router.get("/api/memory/world")
def world_model(request: Request, entity_type: str = "", limit: int = 100) -> dict[str, Any]:
    """S4 world model: entities and edges."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"entities": [], "edges": []}
    return {
        "entities": tm.s4.list_entities(entity_type=entity_type if entity_type else "", limit=limit),
        "edges": tm.s4.list_edges(limit=limit),
    }


@router.get("/api/memory/search")
def semantic_search(request: Request, q: str = "", limit: int = 10, alpha: float = 0.3) -> dict[str, Any]:
    """语义搜索记忆 — 混合 FTS5 + 向量检索。

    Args:
        q: 搜索查询
        limit: 返回结果数 (默认 10, 最大 50)
        alpha: BM25 权重 (0.0=纯语义, 1.0=纯关键词, 默认 0.3)

    Returns:
        {"results": [...], "query": str, "total": int}
    """
    if not q.strip():
        return {"results": [], "query": q, "total": 0}

    container = request.app.state.container
    tm = container.tiered_memory
    if tm is None:
        return {"results": [], "query": q, "total": 0}

    limit = min(limit, 50)
    alpha = max(0.0, min(1.0, alpha))

    try:
        results = tm.hybrid_search(q, alpha=alpha, top_k=limit)
        return {
            "results": [
                {
                    "id": r.get("id", ""),
                    "content": r.get("content", "")[:300],
                    "category": r.get("category", ""),
                    "importance": r.get("importance", 0),
                    "created_at": r.get("created_at", ""),
                }
                for r in results
            ],
            "query": q,
            "total": len(results),
            "alpha": alpha,
        }
    except Exception:
        return {"results": [], "query": q, "total": 0, "error": "search failed"}


@router.get("/api/memory/compaction/stats")
def compaction_stats(request: Request) -> dict[str, Any]:
    """记忆压缩统计 — 最后一次维护的结果。"""
    container = request.app.state.container
    ss = container.system_state
    gov = ss.get("last_memory_governor", {})

    return {
        "last_maintenance": gov if gov else {"status": "not_yet_run"},
        "memory_governor_available": container.memory_governor is not None,
    }


@router.get("/api/memory/db-stats")
def db_table_stats(request: Request) -> dict[str, Any]:
    """数据库表统计 — 各表的行数。"""
    container = request.app.state.container
    store = container.store

    if store is None:
        return {"status": "unavailable"}

    tables = [
        "events", "traces", "memory_items",
        "working_memory", "long_term_memory",
        "world_entities", "world_edges",
        "executor_audit",
    ]

    counts = {}
    for table in tables:
        try:
            rows = store.fetchall(f"SELECT COUNT(*) as cnt FROM {table}", ())
            counts[table] = rows[0]["cnt"] if rows else 0
        except Exception:
            counts[table] = -1  # table doesn't exist

    return {"table_counts": counts, "total_tables": len(tables)}
