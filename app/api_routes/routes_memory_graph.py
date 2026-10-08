"""Read-only graph and portable Obsidian notes; no implicit DB/vault writes."""
from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from connectors.obsidian import INDEX, open_uri, zip_notes
from memory.graph_projection import TIERS, dangling_world_relations, memory_node, project_memory
from memory.graph_neighbors import memory_neighbors

router = APIRouter()


def _sources(request: Request) -> tuple:
    container = request.app.state.container
    path = getattr(getattr(container, "store", None), "db_path", None)
    if path is None:
        raise HTTPException(503, "memory database unavailable")
    tiered = getattr(container, "tiered_memory", None)
    session = tiered.s1.list_all() if tiered is not None else None
    # Runtime configuration is injected; never fall back to a production path.
    vault = Path(container.settings.base_dir)
    return Path(path), session, vault


def _decorate(graph: dict, vault: Path) -> dict:
    graph["obsidian"] = {"vault_detected": (vault / ".obsidian").is_dir(),
                         "index_available": (vault / INDEX).is_file(), "index_uri": open_uri(vault),
                         "mode": "one_way_mirror"}
    for node in graph["nodes"]:
        node["obsidian_uri"] = open_uri(vault, node["note_path"]) if (vault / node["note_path"]).is_file() else None
    return graph


@router.get("/api/memory/graph")
def memory_graph(request: Request, limit: int = Query(420, ge=1, le=600),
                 edge_limit: int = Query(1800, ge=1, le=3000),
                 q: str = Query("", max_length=200), tiers: str = "S1,S2,S3,S4,S5") -> dict:
    path, session, vault = _sources(request)
    selected = tuple(dict.fromkeys(tiers.split(",")))
    if not selected or any(tier not in TIERS for tier in selected):
        raise HTTPException(422, "unknown memory tier")
    try:
        return _decorate(project_memory(path, limit=limit, edge_limit=edge_limit, query=q,
                                        tiers=selected, session=session), vault)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "memory graph unavailable; database could not be read") from exc


@router.get("/api/memory/graph/dangling")
def graph_dangling(request: Request, limit: int = Query(50, ge=1, le=100),
                   offset: int = Query(0, ge=0, le=2_147_483_647)) -> dict:
    path, _, _ = _sources(request)
    try:
        return dangling_world_relations(path, limit=limit, offset=offset)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "world relation diagnostics unavailable") from exc


@router.get("/api/memory/graph/node")
def graph_node(request: Request, tier: str, record_id: str = Query(max_length=2000)) -> dict:
    path, session, vault = _sources(request)
    if tier not in TIERS:
        raise HTTPException(422, "unknown memory tier")
    try:
        node = memory_node(path, tier, record_id, session=session)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "memory record unavailable") from exc
    if node is None:
        raise HTTPException(404, "memory record not found or expired")
    node["obsidian_uri"] = open_uri(vault, node["note_path"]) if (vault / node["note_path"]).is_file() else None
    return node


@router.get("/api/memory/graph/neighbors")
def graph_neighbors(request: Request, tier: str, record_id: str = Query(min_length=1, max_length=2000),
                    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=3000)) -> dict:
    path, session, vault = _sources(request)
    if tier not in TIERS:
        raise HTTPException(422, "unknown memory tier")
    try:
        result = memory_neighbors(path, tier, record_id, session=session, limit=limit, offset=offset)
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(503, "memory neighborhood unavailable") from exc
    if result is None:
        raise HTTPException(404, "memory record not found or expired")
    return _decorate(result, vault)


@router.get("/api/obsidian/export")
def obsidian_export(request: Request, limit: int = Query(420, ge=1, le=600)) -> Response:
    path, session, _ = _sources(request)
    try:
        graph = project_memory(path, limit=limit, session=session, content_limit=100_000)
        archive = zip_notes(graph)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, "memory export unavailable") from exc
    return Response(archive, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="EVA-Memory.zip"', "Cache-Control": "no-store"})
