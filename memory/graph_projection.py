"""Bounded, read-only memory graph; spatial proximity is never a relation."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from memory.provenance import field_provenance, read_provenance

TIERS = {"S1": "会话记忆", "S2": "工作记忆", "S3": "长期记忆", "S4": "世界模型", "S5": "事件档案"}
TABLES = {"S2": "working_memory", "S3": "long_term_memory", "S4": "world_entities", "S5": "events"}
ORDER = {"S2": "created_at DESC, id", "S3": "importance DESC, created_at DESC, id",
         "S4": "updated_at DESC, id",
         "S5": "(type IN ('user_message', 'agent_response')) DESC, timestamp DESC, id"}
DANGLING_WORLD_FROM = (
    "FROM world_edges e LEFT JOIN world_entities s ON s.id=e.source "
    "LEFT JOIN world_entities t ON t.id=e.target WHERE s.id IS NULL OR t.id IS NULL"
)


@contextmanager
def read_database(path: Path) -> Iterator[sqlite3.Connection]:
    """One consistent SQLite read transaction, with no migrations or bootstrap."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def node_id(tier: str, record_id: str) -> str:
    return tier + ":" + record_id


def note_path(identifier: str, label: str = "") -> str:
    # IDs may contain slashes, markup or Windows reserved names. Never use them as paths.
    title = re.sub(r"[^\w -]", "", label, flags=re.UNICODE).strip()[:24].strip() or "memory"
    return f"EVA-Memory/notes/{identifier[:2]}-{title}-{hashlib.sha256(identifier.encode()).hexdigest()[:20]}.md"


def make_node(tier: str, row: dict, content_limit: int = 1200) -> dict:
    origin = read_provenance(row)
    properties = json_value(row.get("properties_json"), {})
    properties = properties if isinstance(properties, dict) else {}
    payload = json_value(row.get("payload"), {})
    if tier == "S4":
        content = json.dumps(properties, ensure_ascii=False, indent=2)
        label = str(row.get("name") or row["id"])
    elif tier == "S5":
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        message = next((payload[k] for k in ("text", "reply", "message", "content")
                        if isinstance(payload, dict) and isinstance(payload.get(k), str)), "")
        label = str(message or row.get("type") or row["id"])
    else:
        content = str(row.get("content", row.get("value", "")))
        label = str(row.get("summary") or content or row["id"])
    identifier = node_id(tier, str(row["id"]))
    fields = field_provenance(row, ["name", "type", *[f"properties.{key}" for key in properties]]) if tier == "S4" else {}
    refs = {origin.get("source_event_id", ""), str(row.get("source_event_id") or "")}
    refs.update(item.get("source_event_id", "") for item in fields.values())
    return {
        "id": identifier, "record_id": str(row["id"]), "tier": tier,
        "label": " ".join(label.split())[:100], "content": content[:content_limit],
        "content_truncated": len(content) > content_limit,
        "kind": str(row.get("type") or row.get("category") or TIERS[tier]),
        "timestamp": str(row.get("updated_at") or row.get("created_at") or row.get("timestamp") or row.get("ts") or ""),
        "source": str(row.get("source") or origin["source"]),
        "provenance": origin, "field_provenance": fields,
        "source_event_ids": sorted(ref for ref in refs if ref), "note_path": note_path(identifier, " ".join(label.split())),
    }


def _condition(tier: str, now: str) -> tuple[str, list]:
    if tier == "S2":
        return "expires_at > ?", [now]
    if tier == "S3":
        return "status = 'active'", []
    return "1=1", []


def project_memory(path: Path, *, limit: int = 420, edge_limit: int = 1800,
                   query: str = "", tiers: tuple[str, ...] = tuple(TIERS),
                   session: list[tuple[str, Any]] | None = None,
                   content_limit: int = 1200) -> dict:
    if not 1 <= limit <= 600 or not 1 <= edge_limit <= 3000:
        raise ValueError("graph limits exceed supported bounds")
    if not tiers or any(tier not in TIERS for tier in tiers):
        raise ValueError("unknown memory tier")
    query = query.strip()[:200]
    now = datetime.now(timezone.utc).isoformat()
    selected_tiers = set(tiers)
    buckets: dict[str, list[dict]] = {}
    counts: dict[str, dict] = {}
    warnings: list[str] = []
    with read_database(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for tier in TIERS:
            if tier == "S1":
                rows = [{**(copy.deepcopy(value) if isinstance(value, dict) else {"value": value}), "id": key}
                        for key, value in (session or [])]
                matched = [row for row in rows if not query or query.casefold() in json.dumps(row, ensure_ascii=False).casefold()]
                counts[tier] = {"available": session is not None, "total": len(rows), "matched": len(matched)}
                buckets[tier] = [make_node(tier, row, content_limit) for row in matched[:limit]] if tier in selected_tiers else []
                continue
            table = TABLES[tier]
            if table not in tables:
                counts[tier] = {"available": False, "total": 0, "matched": 0}
                buckets[tier] = []
                warnings.append(f"{tier}: table unavailable")
                continue
            where, params = _condition(tier, now)
            total = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
            if query:
                columns = {"S2": ["content", "summary", "source"], "S3": ["content", "category"],
                           "S4": ["name", "id", "type", "properties_json"], "S5": ["payload", "type", "source"]}[tier]
                where += " AND (" + " OR ".join(f"instr(lower({column}), lower(?)) > 0" for column in columns) + ")"
                params += [query] * len(columns)
            matched_count = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
            counts[tier] = {"available": True, "total": total, "matched": matched_count}
            rows = connection.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {ORDER[tier]} LIMIT ?", [*params, limit]) if tier in selected_tiers else []
            buckets[tier] = [make_node(tier, dict(row), content_limit) for row in rows]

        # Round-robin avoids the event archive starving the smaller memory tiers.
        seeds = [bucket[i] for i in range(limit) for tier in ("S4", "S3", "S2", "S1", "S5")
                 if len(bucket := buckets[tier]) > i]
        reserve = min(40, limit // 5) if "S5" in selected_tiers and not query else 0
        nodes = {node["id"]: node for node in seeds[:limit - reserve]}
        refs = sorted({ref for node in nodes.values() for ref in node["source_event_ids"]})
        if reserve and "events" in tables:
            for ref in refs:
                if len(nodes) >= limit:
                    break
                if node_id("S5", ref) not in nodes:
                    row = connection.execute("SELECT * FROM events WHERE id = ?", (ref,)).fetchone()
                    if row:
                        node = make_node("S5", dict(row), content_limit)
                        nodes[node["id"]] = node
        for node in seeds:
            if len(nodes) >= limit:
                break
            nodes.setdefault(node["id"], node)

        edges: list[dict] = []
        edge_total = 0
        world_ids = [node["record_id"] for node in nodes.values() if node["tier"] == "S4"]
        eligible_world_edges = 0
        dangling_world_edges = 0
        if "world_edges" in tables:
            edge_total = connection.execute("SELECT COUNT(*) FROM world_edges").fetchone()[0]
            if "world_entities" in tables:
                dangling_world_edges = connection.execute(
                    "SELECT COUNT(*) " + DANGLING_WORLD_FROM
                ).fetchone()[0]
            if world_ids:
                slots = ",".join("?" for _ in world_ids)
                condition = f"source IN ({slots}) AND target IN ({slots})"
                eligible_world_edges = connection.execute(f"SELECT COUNT(*) FROM world_edges WHERE {condition}", world_ids * 2).fetchone()[0]
                rows = connection.execute(f"SELECT * FROM world_edges WHERE {condition} ORDER BY source, target, relation LIMIT ?", [*world_ids, *world_ids, edge_limit])
                for row in rows:
                    row = dict(row)
                    try:
                        weight = float(row.get("weight", 1))
                    except (ValueError, TypeError):
                        weight = 1.0
                    edges.append({"source": node_id("S4", row["source"]), "target": node_id("S4", row["target"]),
                                  "relation": row["relation"], "kind": "stored_relation",
                                  "weight": weight if math.isfinite(weight) else 1.0,
                                  "provenance": read_provenance(row, source_fallback=False),
                                  "timestamp": row.get("updated_at", "")})
        origin_edges = [{"source": node["id"], "target": node_id("S5", ref), "relation": "source_event",
                         "kind": "provenance", "weight": 0.5}
                        for node in nodes.values() for ref in node["source_event_ids"]
                        if node_id("S5", ref) in nodes and node_id("S5", ref) != node["id"]]
        edges.extend(origin_edges[:max(0, edge_limit - len(edges))])
        for edge in edges:
            edge["id"] = hashlib.sha256(json.dumps([edge["source"], edge["target"], edge["relation"], edge["kind"]], ensure_ascii=False).encode()).hexdigest()
        for tier, count in counts.items():
            count["shown"] = sum(node["tier"] == tier for node in nodes.values())
        matched = sum(count["matched"] for tier, count in counts.items() if tier in selected_tiers)
        return {
            "schema_version": "1.0", "generated_at": now, "nodes": list(nodes.values()), "edges": edges,
            "counts": counts, "query": query, "tiers": list(tiers), "warnings": warnings,
            "scope": {"node_limit": limit, "edge_limit": edge_limit, "matched_nodes": matched,
                      "nodes_truncated": matched > len(nodes), "stored_world_edges": edge_total,
                      "eligible_world_edges": eligible_world_edges,
                      "dangling_world_edges": dangling_world_edges,
                      "edges_truncated": eligible_world_edges + len(origin_edges) > len(edges),
                      "session_available": session is not None,
                      "selection": "balanced tiers; recent world/working, important long-term, conversation events first; explicit source events included"},
        }


def dangling_world_relations(path: Path, *, limit: int = 50, offset: int = 0) -> dict:
    """Inspect absent database endpoints, not nodes omitted from a projection.

    Each page and its count share one read transaction. Pages requested later
    observe current data, so concurrent writes can change offset pagination.
    Missing tables raise SQLite errors rather than reporting a healthy graph.
    """
    if not 1 <= limit <= 100 or not 0 <= offset <= 2_147_483_647:
        raise ValueError("diagnostic page limits exceed supported bounds")
    with read_database(path) as connection:
        total = connection.execute("SELECT COUNT(*) " + DANGLING_WORLD_FROM).fetchone()[0]
        rows = connection.execute(
            "SELECT e.*, s.id AS source_exists, s.name AS source_name, "
            "t.id AS target_exists, t.name AS target_name " + DANGLING_WORLD_FROM +
            " ORDER BY e.source, e.target, e.relation LIMIT ? OFFSET ?", (limit, offset),
        )
        items = []
        for row in rows:
            row = dict(row)
            items.append({
                "source": {"record_id": row["source"], "label": row["source_name"],
                           "missing": row["source_exists"] is None},
                "target": {"record_id": row["target"], "label": row["target_name"],
                           "missing": row["target_exists"] is None},
                "relation": row["relation"], "timestamp": row["updated_at"],
                "provenance": read_provenance(row, source_fallback=False),
            })
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def memory_node(path: Path, tier: str, record_id: str, *, session: list | None = None) -> dict | None:
    if tier == "S1":
        value = next((value for key, value in (session or []) if key == record_id), None)
        return make_node(tier, {**value, "id": record_id}, 100_000) if isinstance(value, dict) else None
    if tier not in TABLES:
        return None
    with read_database(path) as connection:
        where, params = _condition(tier, datetime.now(timezone.utc).isoformat())
        row = connection.execute(f"SELECT * FROM {TABLES[tier]} WHERE id = ? AND {where}", [record_id, *params]).fetchone()
        return make_node(tier, dict(row), 100_000) if row else None
