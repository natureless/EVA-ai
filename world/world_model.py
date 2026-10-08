"""World model as an in-memory graph backed by S4 persistent storage.

Upgrades the old flat dataclass to WorldModelGraph: entities + typed edges
that accumulate across cognition loops. All old fields (focus, mode, etc.)
are preserved as top-level attributes for backward compatibility.

Usage::

    wm = WorldModelGraph()
    wm.upsert_entity("task", "Write tests", {"status": "active"})
    wm.link("user", "task_write_tests", "owns", weight=1.0)
    wm.flush(s4_store)  # persist dirty entities/edges to SQLite
    wm.load_from_store(s4_store)  # restore from SQLite
"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from heapq import nlargest
import json
import threading
from typing import Any
from uuid import uuid4

from memory.context_evidence import ViewReference, context_hash

from memory.provenance import (
    EpistemicStatus,
    aggregate_provenance,
    field_provenance,
    provenance,
    read_provenance,
)


# ── Entity & Edge ──────────────────────────────────────────


@dataclass
class Entity:
    eid: str
    type: str  # task | person | file | project | dependency | risk | blocker
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    provenance: dict[str, Any] = field(default_factory=provenance)
    field_provenance: dict[str, dict] = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    relation: str  # owns | assigned_to | depends_on | blocks | references
    weight: float = 1.0
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    provenance: dict[str, Any] = field(default_factory=provenance)


# ── WorldModelGraph ────────────────────────────────────────


def _make_eid(entity_type: str, name: str) -> str:
    """Deterministic entity id: type_name (no random suffix — enables cross-module matching)."""
    slug = name.lower().replace(" ", "_").replace("-", "_").replace("/", "_")[:60]
    return f"{entity_type}_{slug}"


ENTITY_TYPES = {"task", "person", "file", "project", "dependency", "risk", "blocker"}

EdgeKey = tuple[str, str, str]


def _record_time(value: str) -> datetime | None:
    """Unknown/naive timestamps cannot establish ordering between stores."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _prefer_incoming(current: str, incoming: str, *, fallback: bool) -> bool:
    old, new = _record_time(current), _record_time(incoming)
    if old is not None and new is not None and old != new:
        return new > old
    return fallback


def _restore_entity(row: dict[str, Any]) -> Entity:
    if row["type"] not in ENTITY_TYPES:
        raise ValueError(f"Unknown entity type: {row['type']}")
    props = deepcopy(row.get("properties", {}))
    origins = field_provenance(row, ["name", *[f"properties.{key}" for key in props]])
    return Entity(
        eid=row["id"],
        type=row["type"],
        name=row["name"],
        properties=props,
        updated_at=row.get("updated_at", ""),
        provenance=aggregate_provenance(origins),
        field_provenance=origins,
    )


def _restore_edge(row: dict[str, Any]) -> Edge:
    return Edge(
        source=row["source"],
        target=row["target"],
        relation=row["relation"],
        weight=float(row.get("weight", 1.0)),
        updated_at=row.get("updated_at", ""),
        provenance=read_provenance(row, source_fallback=False),
    )


@dataclass
class WorldModelGraph:
    """Accumulating world model backed by S4 WorldModelStore."""

    # ── top-level (backward-compat) ────────────────────────
    focus: str = "idle"
    mode: str = "active"
    last_reply: str = ""
    last_selected_agent: str = ""
    last_loop_id: str = ""
    last_loop_at: str | None = None
    last_user_message_at: str | None = None
    last_reminder_at: str | None = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── graph internals ────────────────────────────────────
    _entities: dict[str, Entity] = field(default_factory=dict)
    _edges: dict[EdgeKey, Edge] = field(default_factory=dict)
    _dirty_entities: set[str] = field(default_factory=set)
    _dirty_edges: set[EdgeKey] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _view_scope: str = field(default_factory=lambda: f"world_{uuid4().hex}")
    _revision: int = 0

    def __setattr__(self, name, value):
        # Legacy top-level assignments also pass through the graph lock.
        tracked = {
            "focus",
            "mode",
            "last_reply",
            "last_selected_agent",
            "last_loop_id",
            "last_loop_at",
            "last_user_message_at",
            "last_reminder_at",
            "updated_at",
        }
        lock = self.__dict__.get("_lock")
        if name in tracked and lock is not None and "_revision" in self.__dict__:
            with lock:
                changed = self.__dict__.get(name) != value
                object.__setattr__(self, name, value)
                if changed:
                    object.__setattr__(self, "_revision", self._revision + 1)
        else:
            object.__setattr__(self, name, value)

    # ── entity ops ─────────────────────────────────────────

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        *,
        eid: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> str:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"Unknown entity type: {entity_type}")
        eid = eid or _make_eid(entity_type, name)
        incoming_origin = read_provenance({"provenance": origin})

        with self._lock:
            # merge with existing entity if present
            existing = self._entities.get(eid)
            if existing:
                existing.name = name
                existing.field_provenance["name"] = deepcopy(incoming_origin)
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                if properties:
                    existing.properties.update(deepcopy(properties))
                    for key in properties:
                        existing.field_provenance[f"properties.{key}"] = deepcopy(
                            incoming_origin
                        )
                existing.provenance = aggregate_provenance(existing.field_provenance)
                self._dirty_entities.add(eid)
                self._revision += 1
                return eid

            now = datetime.now(timezone.utc).isoformat()
            props = deepcopy(properties or {})
            origins = {
                key: deepcopy(incoming_origin)
                for key in ["name", *[f"properties.{key}" for key in props]]
            }
            if entity_type == "task":
                for key, value in {
                    "status": "active",
                    "priority": "medium",
                    "created_at": now,
                }.items():
                    if key not in props:
                        props[key] = value
                        origins[f"properties.{key}"] = provenance(
                            EpistemicStatus.WORKING_MODEL, source="world_defaults"
                        )
            self._entities[eid] = Entity(
                eid=eid,
                type=entity_type,
                name=name,
                properties=props,
                updated_at=now,
                provenance=aggregate_provenance(origins),
                field_provenance=origins,
            )
            self._dirty_entities.add(eid)
            self._revision += 1
            return eid

    def get_entity(self, eid: str) -> Entity | None:
        with self._lock:
            return deepcopy(self._entities.get(eid))

    def list_entities(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent entities as dicts (for API consumption)."""
        with self._lock:
            items = sorted(
                self._entities.values(),
                key=lambda e: e.updated_at,
                reverse=True,
            )[:limit]
            return [
                {
                    "id": e.eid,
                    "type": e.type,
                    "name": e.name,
                    "properties_json": json.dumps(e.properties, ensure_ascii=False),
                    "updated_at": e.updated_at,
                    "provenance": deepcopy(e.provenance),
                    "field_provenance": deepcopy(e.field_provenance),
                }
                for e in items
            ]

    def get_entities_by_type(self, entity_type: str) -> list[Entity]:
        with self._lock:
            return [
                deepcopy(e) for e in self._entities.values() if e.type == entity_type
            ]

    def remove_entity(self, eid: str) -> bool:
        with self._lock:
            if eid in self._entities:
                del self._entities[eid]
                self._dirty_entities.discard(eid)
                self._revision += 1
                return True
            return False

    # ── edge ops ───────────────────────────────────────────

    def link(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        weight: float = 1.0,
        origin: dict[str, Any] | None = None,
    ) -> Edge:
        with self._lock:
            key = (source, target, relation)
            existing = self._edges.get(key)
            if existing:
                existing.weight = weight
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                existing.provenance = read_provenance({"provenance": origin})
                self._dirty_edges.add(key)
                self._revision += 1
                return deepcopy(existing)
            edge = Edge(
                source=source,
                target=target,
                relation=relation,
                weight=weight,
                provenance=read_provenance({"provenance": origin}),
            )
            self._edges[key] = edge
            self._dirty_edges.add(key)
            self._revision += 1
            return deepcopy(edge)

    def get_edges(self, eid: str = "", relation: str = "") -> list[Edge]:
        with self._lock:
            result = deepcopy(list(self._edges.values()))
        if eid:
            result = [e for e in result if e.source == eid or e.target == eid]
        if relation:
            result = [e for e in result if e.relation == relation]
        return result

    def get_neighbors(self, eid: str) -> list[Entity]:
        with self._lock:
            neighbors: list[Entity] = []
            for edge in self.get_edges(eid):
                if edge.source == eid and edge.target in self._entities:
                    neighbors.append(deepcopy(self._entities[edge.target]))
                elif edge.target == eid and edge.source in self._entities:
                    neighbors.append(deepcopy(self._entities[edge.source]))
            return neighbors

    # ── derived properties ─────────────────────────────────

    @property
    def active_tasks(self) -> list[dict[str, Any]]:
        tasks = self.get_entities_by_type("task")
        return [
            {
                **deepcopy(t.properties),
                "id": t.eid,
                "name": t.name,
                "provenance": deepcopy(t.provenance),
                "field_provenance": deepcopy(t.field_provenance),
            }
            for t in tasks
            if t.properties.get("status") == "active"
        ]

    @property
    def recent_entities(self) -> list[str]:
        sorted_entities = sorted(
            self._entities.values(),
            key=lambda e: e.updated_at,
            reverse=True,
        )
        return [e.name for e in sorted_entities[:10]]

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def counts(self) -> dict[str, int]:
        """Constant-space counters without serializing graph contents."""
        with self._lock:
            return {"entities": len(self._entities), "edges": len(self._edges)}

    def context_projection(
        self, *, task_limit: int = 10, entity_limit: int = 10
    ) -> dict[str, Any]:
        """Small context view, with no edge traversal or arbitrary properties.

        Recent selection scans entities in O(N log K) time and O(K) space.
        This bounds output/allocation, not total read time for very large N.
        """
        for value in (task_limit, entity_limit):
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("projection limits must be integers from 0 to 100")
        with self._lock:
            tasks = []
            if task_limit:
                for entity in self._entities.values():
                    if (
                        entity.type != "task"
                        or entity.properties.get("status") != "active"
                    ):
                        continue
                    task = {"id": entity.eid[:160], "name": entity.name[:160]}
                    for key in ("status", "priority", "deadline"):
                        field_value = entity.properties.get(key)
                        if isinstance(field_value, str):
                            task[key] = field_value[:64]
                    task.update(
                        self._context_origins(
                            entity,
                            [
                                "name",
                                *[
                                    f"properties.{key}"
                                    for key in ("status", "priority", "deadline")
                                    if key in task
                                ],
                            ],
                        )
                    )
                    tasks.append(task)
                    if len(tasks) == task_limit:
                        break
            recent = (
                nlargest(
                    entity_limit,
                    self._entities.values(),
                    key=lambda entity: entity.updated_at or "",
                )
                if entity_limit
                else []
            )
            projection = {
                "schema_version": 2,
                "focus": self.focus[:160],
                "mode": self.mode[:64],
                "last_loop_id": self.last_loop_id[:160],
                "active_tasks": tasks,
                "recent_entities": [entity.name[:160] for entity in recent],
                "recent_entity_records": [
                    {
                        "id": entity.eid[:160],
                        "name": entity.name[:160],
                        **self._context_origins(entity, ["name"]),
                    }
                    for entity in recent
                ],
                "counts": self.counts(),
            }
            projection["reference"] = ViewReference(
                scope=self._view_scope,
                revision=self._revision,
                kind="world_context_projection",
                integrity_hash=context_hash(projection),
            ).model_dump(mode="json")
            return projection

    @staticmethod
    def _context_origins(entity: Entity, fields: list[str]) -> dict:
        origins = field_provenance(
            {
                "provenance": entity.provenance,
                "field_provenance": entity.field_provenance,
            },
            fields,
        )
        for origin in origins.values():
            origin["source"] = origin["source"][:160]
            origin["source_event_id"] = origin["source_event_id"][:160]
        return {
            "provenance": aggregate_provenance(origins),
            "field_provenance": origins,
        }

    # ── S4 persistence ─────────────────────────────────────

    def flush(self, s4_store: Any) -> None:
        """Persist dirty entities and edges to S4 WorldModelStore."""
        with self._lock:
            for eid in self._dirty_entities:
                e = self._entities[eid]
                s4_store.upsert_entity(
                    entity_id=eid,
                    entity_type=e.type,
                    name=e.name,
                    properties=e.properties,
                    origin=e.provenance,
                    field_origins=e.field_provenance,
                )
            for key in self._dirty_edges:
                edge = self._edges[key]
                s4_store.upsert_edge(
                    source=edge.source,
                    target=edge.target,
                    relation=edge.relation,
                    weight=edge.weight,
                    origin=edge.provenance,
                )
            self._dirty_entities.clear()
            self._dirty_edges.clear()

    def load_from_store(self, s4_store: Any) -> None:
        """Merge all S4 records during startup, before producers start.

        Compare valid record timestamps; S4 wins ties or unknown ordering.
        Snapshot-only and unflushed local records survive. Stage the complete
        read before committing so a failed page cannot leave a partial graph.
        This does not provide a transaction across concurrent external writers.
        """
        with self._lock:
            entities = dict(self._entities)
            edges = dict(self._edges)
            for row in s4_store.iter_entities():
                incoming = _restore_entity(row)
                current = entities.get(incoming.eid)
                if incoming.eid not in self._dirty_entities and (
                    current is None
                    or _prefer_incoming(
                        current.updated_at,
                        incoming.updated_at,
                        fallback=True,
                    )
                ):
                    entities[incoming.eid] = incoming
            for row in s4_store.iter_edges():
                incoming_edge = _restore_edge(row)
                key = (
                    incoming_edge.source,
                    incoming_edge.target,
                    incoming_edge.relation,
                )
                current_edge = edges.get(key)
                if key not in self._dirty_edges and (
                    current_edge is None
                    or _prefer_incoming(
                        current_edge.updated_at,
                        incoming_edge.updated_at,
                        fallback=True,
                    )
                ):
                    edges[key] = incoming_edge
            if entities != self._entities or edges != self._edges:
                self._entities = entities
                self._edges = edges
                self._revision += 1

    # ── snapshot compatibility ─────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot compatible with old WorldModel format.

        Acquires the graph lock so snapshot captures a consistent point-in-time
        even when the cognition loop is concurrently mutating entities/edges.
        """
        with self._lock:
            return {
                "graph_schema_version": 2,
                "focus": self.focus,
                "mode": self.mode,
                "active_tasks": deepcopy(self.active_tasks),
                "recent_entities": self.recent_entities,
                "last_reply": self.last_reply,
                "last_selected_agent": self.last_selected_agent,
                "last_loop_id": self.last_loop_id,
                "last_loop_at": self.last_loop_at,
                "last_user_message_at": self.last_user_message_at,
                "last_reminder_at": self.last_reminder_at,
                "updated_at": self.updated_at,
                "entities": [
                    {
                        "id": e.eid,
                        "type": e.type,
                        "name": e.name,
                        "properties": deepcopy(e.properties),
                        "updated_at": e.updated_at,
                        "provenance": deepcopy(e.provenance),
                        "field_provenance": deepcopy(e.field_provenance),
                    }
                    for e in self._entities.values()
                ],
                "edges": [
                    {
                        "source": e.source,
                        "target": e.target,
                        "relation": e.relation,
                        "weight": e.weight,
                        "updated_at": e.updated_at,
                        "provenance": deepcopy(e.provenance),
                    }
                    for e in self._edges.values()
                ],
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldModelGraph":
        """Read v2, v1 or unversioned snapshots without inventing sources/times.

        Duplicate records use newer known timestamps; ties or unknown ordering
        retain the first entry. S4 authority is applied separately at startup.
        """
        version = data.get("graph_schema_version", 0)
        if type(version) is not int or version not in (0, 1, 2):
            raise ValueError("unsupported world graph snapshot version")
        wm = cls(
            focus=data.get("focus", "idle"),
            mode=data.get("mode", "active"),
            last_reply=data.get("last_reply", ""),
            last_selected_agent=data.get("last_selected_agent", ""),
            last_loop_id=data.get("last_loop_id", ""),
            last_loop_at=data.get("last_loop_at"),
            last_user_message_at=data.get("last_user_message_at"),
            last_reminder_at=data.get("last_reminder_at"),
            updated_at=data.get("updated_at", ""),
        )
        # restore entities from snapshot
        for ed in data.get("entities", []):
            incoming = _restore_entity(ed)
            current = wm._entities.get(incoming.eid)
            if current is None or _prefer_incoming(
                current.updated_at,
                incoming.updated_at,
                fallback=False,
            ):
                wm._entities[incoming.eid] = incoming
        # restore edges from snapshot
        for ed in data.get("edges", []):
            incoming_edge = _restore_edge(ed)
            key = (incoming_edge.source, incoming_edge.target, incoming_edge.relation)
            current_edge = wm._edges.get(key)
            if current_edge is None or _prefer_incoming(
                current_edge.updated_at,
                incoming_edge.updated_at,
                fallback=False,
            ):
                wm._edges[key] = incoming_edge
        return wm

    # ── mutation helpers ───────────────────────────────────

    def apply_user_message(self, text: str) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            if text:
                self.focus = text[:80]
            self.last_user_message_at = now
            self.updated_at = now

    def apply_agent_result(
        self, *, reply: str, selected_agent: str, loop_id: str
    ) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self.last_reply = reply
            self.last_selected_agent = selected_agent
            self.last_loop_id = loop_id
            self.last_loop_at = now
            self.updated_at = now

    def apply_reminder(self) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self.last_reminder_at = now
            self.updated_at = now
