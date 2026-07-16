from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/memory/recent")
def recent_memory(request: Request, limit: int = 20) -> dict:
    container = request.app.state.container
    memory_api = container["memory_api"]
    return {"items": memory_api.get_recent_memories(limit=limit)}


@router.get("/api/debug/trace")
def recent_trace(request: Request, limit: int = 20) -> dict:
    container = request.app.state.container
    memory_api = container["memory_api"]
    return {"items": memory_api.get_recent_traces(limit=limit)}


@router.get("/api/events/recent")
def recent_events(request: Request, limit: int = 20) -> dict:
    container = request.app.state.container
    memory_api = container["memory_api"]
    return {"items": memory_api.get_recent_events(limit=limit)}
