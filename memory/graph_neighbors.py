"""Read one-hop stored relations independently of the initial graph projection."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from memory.graph_projection import TABLES, TIERS, _condition, make_node, node_id, read_database
from memory.provenance import read_provenance


def _edge(source: str, target: str, relation: str, kind: str, **fields) -> dict:
    identity = json.dumps([source, target, relation, kind], ensure_ascii=False)
    return {"id": hashlib.sha256(identity.encode()).hexdigest(), "source": source,
            "target": target, "relation": relation, "kind": kind, **fields}


def memory_neighbors(path: Path, tier: str, record_id: str, *, session: list | None = None,
                     limit: int = 50, offset: int = 0) -> dict | None:
    """Page incident edges in a single read transaction; never infer similarity.

    Offset pages observe separate snapshots. Reverse provenance uses exact JSON
    values followed by make_node's field rules, including legacy column sources.
    At most 5,000 candidate source records are materialized per request; a scan
    limit is reported as incomplete, never as an exhaustive empty neighborhood.
    """
    if tier not in TIERS or not 1 <= limit <= 100 or not 0 <= offset <= 3000:
        raise ValueError("invalid neighborhood request")
    now = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []
    scan_truncated = False
    sessions = {key: copy.deepcopy(value) for key, value in (session or [])}
    with read_database(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def read(t: str, identifier: str) -> dict | None:
            if t == "S1":
                if identifier not in sessions:
                    return None
                value = sessions[identifier]
                return make_node(t, {**(value if isinstance(value, dict) else {"value": value}), "id": identifier})
            table = TABLES[t]
            if table not in tables:
                raise ValueError(f"{t}: table unavailable")
            where, params = _condition(t, now)
            row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND {where}", [identifier, *params]).fetchone()
            return make_node(t, dict(row)) if row else None

        center = read(tier, record_id)
        if center is None:
            return None
        missing_endpoints = 0
        if tier == "S4" and "world_edges" in tables:
            missing_endpoints = connection.execute(
                "SELECT COUNT(*) FROM world_edges e LEFT JOIN world_entities s ON s.id=e.source "
                "LEFT JOIN world_entities t ON t.id=e.target WHERE (e.source=? OR e.target=?) "
                "AND (s.id IS NULL OR t.id IS NULL)", (record_id, record_id),
            ).fetchone()[0]
        origins = []
        if center["source_event_ids"] and "events" not in tables:
            warnings.append("S5: event table unavailable")
        elif "events" in tables:
            for ref in center["source_event_ids"]:
                other = read("S5", ref)
                if other is None:
                    missing_endpoints += 1
                elif other["id"] != center["id"]:
                    origins.append(other)

        def incident():
            nonlocal scan_truncated
            if tier == "S4":
                if "world_edges" not in tables:
                    warnings.append("S4: relation table unavailable")
                else:
                    # Missing endpoints remain diagnostics, never fabricated nodes.
                    rows = connection.execute(
                        "SELECT e.* FROM world_edges e JOIN world_entities s ON s.id=e.source "
                        "JOIN world_entities t ON t.id=e.target WHERE e.source=? OR e.target=? "
                        "ORDER BY e.source,e.target,e.relation", (record_id, record_id),
                    )
                    for raw in rows:
                        row = dict(raw)
                        other = read("S4", row["target"] if row["source"] == record_id else row["source"])
                        try:
                            weight = float(row.get("weight", 1))
                        except (ValueError, TypeError):
                            weight = 1.0
                        yield _edge(node_id("S4", row["source"]), node_id("S4", row["target"]),
                                    row["relation"], "stored_relation", weight=weight if math.isfinite(weight) else 1.0,
                                    provenance=read_provenance(row, source_fallback=False),
                                    timestamp=row.get("updated_at", "")), other

            for other in origins:
                yield _edge(center["id"], other["id"], "source_event", "provenance", weight=.5), other

            if tier != "S5":
                return
            candidates = 0
            for t in TIERS:
                if t == "S1":
                    rows = ({**(value if isinstance(value, dict) else {"value": value}), "id": key}
                            for key, value in sorted(sessions.items()))
                else:
                    table = TABLES[t]
                    if table not in tables:
                        warnings.append(f"{t}: source table unavailable")
                        continue
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                    predicates, refs = [], []
                    if "source_event_id" in columns:
                        predicates.append("source_event_id=?")
                        refs.append(record_id)
                    if "provenance_json" in columns:
                        predicates.append("EXISTS (SELECT 1 FROM json_tree(CASE WHEN json_valid(provenance_json) "
                                          "THEN provenance_json ELSE '{}' END) WHERE key='source_event_id' "
                                          "AND CAST(atom AS TEXT)=?)")
                        refs.append(record_id)
                    if not predicates:
                        continue
                    where, params = _condition(t, now)
                    rows = connection.execute(f"SELECT * FROM {table} WHERE {where} AND ({' OR '.join(predicates)}) ORDER BY id",
                                              [*params, *refs])
                for raw in rows:
                    candidates += 1
                    if candidates > 5000:
                        scan_truncated = True
                        return
                    other = make_node(t, dict(raw))
                    if other["id"] != center["id"] and record_id in other["source_event_ids"]:
                        yield _edge(other["id"], center["id"], "source_event", "provenance", weight=.5), other

        nodes, edges, has_more = {center["id"]: center}, [], False
        for index, (edge, other) in enumerate(incident()):
            if index < offset:
                continue
            if len(edges) == limit:
                has_more = True
                break
            edges.append(edge)
            nodes[other["id"]] = other
        next_offset = offset + len(edges) if has_more and offset + len(edges) <= 3000 else None
        page_limit_reached = has_more and next_offset is None
        return {"schema_version": "1.0", "center": center["id"], "nodes": list(nodes.values()),
                "edges": edges, "limit": limit, "offset": offset, "next_offset": next_offset,
                "has_more": has_more, "generated_at": now, "warnings": warnings,
                "scope": {"hops": 1, "query_independent": True, "session_available": session is not None,
                          "scan_truncated": scan_truncated, "page_limit_reached": page_limit_reached,
                          "missing_endpoints": missing_endpoints,
                          "incomplete": scan_truncated or page_limit_reached or bool(warnings),
                          "selection": "stored incident relations and explicit event sources; active records only; pages may change"}}
