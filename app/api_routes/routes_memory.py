from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/memory/recent")
def recent_memory(request: Request, limit: int = 20) -> dict:
    return {"items": request.app.state.container.memory_api.get_recent_memories(limit=limit)}


@router.get("/api/debug/trace")
def recent_trace(request: Request, limit: int = 20) -> dict:
    return {"items": request.app.state.container.memory_api.get_recent_traces(limit=limit)}


@router.get("/api/events/recent")
def recent_events(request: Request, limit: int = 20) -> dict:
    return {"items": request.app.state.container.memory_api.get_recent_events(limit=limit)}


# ── Tiered Memory Endpoints ─────────────────────────────────

@router.get("/api/memory/tiers")
def memory_tiers(request: Request) -> dict:
    """Statistics for all five memory tiers."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"error": "tiered memory not available"}
    return tm.stats()


@router.get("/api/memory/working")
def working_memory(request: Request, limit: int = 50) -> dict:
    """Recent S2 working memory entries."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"items": []}
    return {"items": tm.s2.list_recent(limit=limit)}


@router.get("/api/memory/longterm")
def longterm_memory(request: Request, limit: int = 50, category: str = "") -> dict:
    """S3 long-term memory entries."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"items": []}
    if category:
        return {"items": tm.s3.list_by_category(category, limit=limit)}
    return {"items": tm.s3.list_recent(limit=limit)}


@router.get("/api/memory/world")
def world_model(request: Request, entity_type: str = "", limit: int = 100) -> dict:
    """S4 world model: entities and edges."""
    tm = request.app.state.container.tiered_memory
    if tm is None:
        return {"entities": [], "edges": []}
    return {
        "entities": tm.s4.list_entities(entity_type=entity_type if entity_type else "", limit=limit),
        "edges": tm.s4.list_edges(limit=limit),
    }
