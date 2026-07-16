"""World model as an in-memory graph backed by S4 persistent storage.

Upgrades the old flat dataclass to WorldModelGraph: entities + typed edges
that accumulate across cognition loops. All old fields (focus, mode, etc.)
are preserved as top-level attributes for backward compatibility.

Usage::

    wm = WorldModelGraph(store=s4_store)
    wm.upsert_entity("task", "Write tests", {"status": "active"})
    wm.link("user", "task_write_tests", "owns", weight=1.0)
    wm.flush(s4_store)  # persist dirty entities/edges to SQLite
    wm.load_from_store(s4_store)  # restore from SQLite
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Entity & Edge ──────────────────────────────────────────

@dataclass
class Entity:
    eid: str
    type: str     # task | person | file | project | dependency | risk | blocker
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Edge:
    source: str
    target: str
    relation: str   # owns | assigned_to | depends_on | blocks | references
    weight: float = 1.0
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── WorldModelGraph ────────────────────────────────────────

def _make_eid(entity_type: str, name: str) -> str:
    """Deterministic entity id: type_name (no random suffix — enables cross-module matching)."""
    slug = name.lower().replace(" ", "_").replace("-", "_").replace("/", "_")[:60]
    return f"{entity_type}_{slug}"


ENTITY_TYPES = {"task", "person", "file", "project", "dependency", "risk", "blocker"}


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
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── graph internals ────────────────────────────────────
    _entities: dict[str, Entity] = field(default_factory=dict)
    _edges: list[Edge] = field(default_factory=list)
    _dirty_entities: set[str] = field(default_factory=set)
    _dirty_edges: list[Edge] = field(default_factory=list)

    # ── entity ops ─────────────────────────────────────────

    def upsert_entity(
        self, entity_type: str, name: str, properties: dict[str, Any] | None = None,
        *, eid: str | None = None,
    ) -> str:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"Unknown entity type: {entity_type}")
        eid = eid or _make_eid(entity_type, name)

        # merge with existing entity if present
        existing = self._entities.get(eid)
        if existing:
            existing.name = name
            existing.updated_at = datetime.now(timezone.utc).isoformat()
            if properties:
                existing.properties.update(properties)
            existing.properties.setdefault("status", "active")
            self._dirty_entities.add(eid)
            return eid

        now = datetime.now(timezone.utc).isoformat()
        props = dict(properties or {})
        if entity_type == "task":
            props.setdefault("status", "active")
            props.setdefault("priority", "medium")
            props.setdefault("created_at", now)
        self._entities[eid] = Entity(
            eid=eid, type=entity_type, name=name,
            properties=props, updated_at=now,
        )
        self._dirty_entities.add(eid)
        return eid

    def get_entity(self, eid: str) -> Entity | None:
        return self._entities.get(eid)

    def get_entities_by_type(self, entity_type: str) -> list[Entity]:
        return [e for e in self._entities.values() if e.type == entity_type]

    def remove_entity(self, eid: str) -> bool:
        if eid in self._entities:
            del self._entities[eid]
            self._dirty_entities.discard(eid)
            return True
        return False

    # ── edge ops ───────────────────────────────────────────

    def link(self, source: str, target: str, relation: str, *, weight: float = 1.0) -> Edge:
        edge = Edge(
            source=source, target=target, relation=relation,
            weight=weight,
        )
        self._edges.append(edge)
        self._dirty_edges.append(edge)
        return edge

    def get_edges(self, eid: str = "", relation: str = "") -> list[Edge]:
        result = self._edges
        if eid:
            result = [e for e in result if e.source == eid or e.target == eid]
        if relation:
            result = [e for e in result if e.relation == relation]
        return result

    def get_neighbors(self, eid: str) -> list[Entity]:
        neighbors: list[Entity] = []
        for edge in self._edges:
            if edge.source == eid and edge.target in self._entities:
                neighbors.append(self._entities[edge.target])
            elif edge.target == eid and edge.source in self._entities:
                neighbors.append(self._entities[edge.source])
        return neighbors

    # ── derived properties ─────────────────────────────────

    @property
    def active_tasks(self) -> list[dict[str, Any]]:
        tasks = self.get_entities_by_type("task")
        return [
            {"id": t.eid, "name": t.name, **t.properties}
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

    # ── S4 persistence ─────────────────────────────────────

    def flush(self, s4_store) -> None:
        """Persist dirty entities and edges to S4 WorldModelStore."""
        for eid in self._dirty_entities:
            e = self._entities[eid]
            s4_store.upsert_entity(
                entity_id=eid, entity_type=e.type,
                name=e.name, properties=e.properties,
            )
        for edge in self._dirty_edges:
            s4_store.upsert_edge(
                source=edge.source, target=edge.target,
                relation=edge.relation, weight=edge.weight,
            )
        self._dirty_entities.clear()
        self._dirty_edges.clear()

    def load_from_store(self, s4_store) -> None:
        """Restore graph from S4 WorldModelStore."""
        entities = s4_store.list_entities(limit=5000)
        for row in entities:
            eid = row["id"]
            self._entities[eid] = Entity(
                eid=eid, type=row["type"], name=row["name"],
                properties=row.get("properties", {}),
                updated_at=row.get("updated_at", ""),
            )
        edges = s4_store.list_edges(limit=5000)
        for row in edges:
            self._edges.append(Edge(
                source=row["source"], target=row["target"],
                relation=row["relation"],
                weight=float(row.get("weight", 1.0)),
                updated_at=row.get("updated_at", ""),
            ))

    # ── snapshot compatibility ─────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot compatible with old WorldModel format."""
        return {
            "focus": self.focus,
            "mode": self.mode,
            "active_tasks": self.active_tasks,
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
                    "id": e.eid, "type": e.type, "name": e.name,
                    "properties": e.properties, "updated_at": e.updated_at,
                }
                for e in self._entities.values()
            ],
            "edges": [
                {
                    "source": e.source, "target": e.target,
                    "relation": e.relation, "weight": e.weight,
                }
                for e in self._edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldModelGraph":
        """Create from snapshot dict. Accepts old format (no entities/edges)."""
        wm = cls(
            focus=data.get("focus", "idle"),
            mode=data.get("mode", "active"),
            last_reply=data.get("last_reply", ""),
            last_selected_agent=data.get("last_selected_agent", ""),
            last_loop_id=data.get("last_loop_id", ""),
            last_loop_at=data.get("last_loop_at"),
            last_user_message_at=data.get("last_user_message_at"),
            last_reminder_at=data.get("last_reminder_at"),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )
        # restore entities from snapshot
        for ed in data.get("entities", []):
            wm.upsert_entity(
                entity_type=ed["type"], name=ed["name"],
                properties=ed.get("properties", {}),
                eid=ed["id"],
            )
        wm._dirty_entities.clear()  # loaded from snapshot, not dirty
        # restore edges from snapshot
        for ed in data.get("edges", []):
            wm._edges.append(Edge(
                source=ed["source"], target=ed["target"],
                relation=ed["relation"],
                weight=float(ed.get("weight", 1.0)),
            ))
        return wm

    # ── mutation helpers ───────────────────────────────────

    def apply_user_message(self, text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if text:
            self.focus = text[:80]
        self.last_user_message_at = now
        self.updated_at = now

    def apply_agent_result(self, *, reply: str, selected_agent: str, loop_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_reply = reply
        self.last_selected_agent = selected_agent
        self.last_loop_id = loop_id
        self.last_loop_at = now
        self.updated_at = now

    def apply_reminder(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_reminder_at = now
        self.updated_at = now
