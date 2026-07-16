"""Rule-based entity and relation extraction from agent replies.

No LLM dependency — pattern matching on common text structures.
Used by the cognition loop to auto-populate the WorldModelGraph.
"""

import re


# ── Entity extraction patterns ─────────────────────────────

TASK_PATTERNS = [
    re.compile(r"/task[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"TODO[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"任务[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"\[task\]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"待办[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
]

PERSON_PATTERNS = [
    re.compile(r"@(\w{2,30})"),
    re.compile(r"分配给[:：]\s*@?(\w{2,30})"),
    re.compile(r"assigned to[:：]?\s*@?(\w{2,30})", re.IGNORECASE),
]

FILE_PATTERNS = [
    re.compile(r"`([^`]{2,100}\.\w{2,8})`(?:\s*[:：]\s*(.+?))?(?:\n|$)"),
    re.compile(r"文件[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"file[:：]\s*(.+?\.\w{2,8})", re.IGNORECASE),
]

RISK_MARKERS = ["风险", "risk", "blocker", "阻塞", "critical", "严重"]
DEPENDENCY_MARKERS = ["依赖", "depends on", "requires", "需要先", "前置条件"]


class EntityExtractor:
    """Rule-based extraction of typed entities from text."""

    def extract_entities(self, text: str) -> list[dict]:
        """Return list of {type, name, properties} dicts."""
        entities: list[dict] = []
        seen = set()

        # tasks
        for pattern in TASK_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip().rstrip(".!！。")
                key = f"task_{name}"
                if key not in seen and len(name) > 1:
                    seen.add(key)
                    entities.append({
                        "type": "task", "name": name,
                        "properties": {"status": "active", "source": "agent_reply"},
                    })

        # persons
        for pattern in PERSON_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                key = f"person_{name}"
                if key not in seen and len(name) > 1:
                    seen.add(key)
                    entities.append({
                        "type": "person", "name": name, "properties": {},
                    })

        # files
        for pattern in FILE_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                key = f"file_{name}"
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        "type": "file", "name": name, "properties": {},
                    })

        # risks (keyword match in sentences)
        for sentence in re.split(r"[.!！。\n]", text):
            sentence_lower = sentence.strip().lower()
            if any(m in sentence_lower for m in RISK_MARKERS):
                name = sentence.strip()[:80]
                key = f"risk_{name[:40]}"
                if key not in seen and len(name) > 3:
                    seen.add(key)
                    entities.append({
                        "type": "risk", "name": name, "properties": {"detected_in": "agent_reply"},
                    })

        return entities

    def extract_relations(
        self, entities: list[dict], text: str
    ) -> list[dict]:
        """Infer {source, target, relation} from co-occurrence and keywords."""
        relations: list[dict] = []

        if not entities or len(entities) < 2:
            return relations

        # task entities get auto-linked to "user"
        for e in entities:
            if e["type"] == "task":
                relations.append({
                    "source": "user",
                    "target": _eid(e),
                    "relation": "created",
                    "weight": 0.9,
                })

        # detect dependency markers between tasks
        tasks = [e for e in entities if e["type"] == "task"]
        for i, t1 in enumerate(tasks):
            for t2 in tasks[i + 1:]:
                if any(m in text.lower() for m in DEPENDENCY_MARKERS):
                    if t1["name"] in text.lower():
                        relations.append({
                            "source": _eid(t2),
                            "target": _eid(t1),
                            "relation": "depends_on",
                            "weight": 0.6,
                        })

        # assign persons to tasks
        persons = [e for e in entities if e["type"] == "person"]
        for task in tasks:
            for person in persons:
                if person["name"].lower() in text.lower():
                    relations.append({
                        "source": _eid(task),
                        "target": _eid(person),
                        "relation": "assigned_to",
                        "weight": 0.7,
                    })

        return relations

    def extract_from_reply(self, reply: str) -> tuple[list[dict], list[dict]]:
        """Convenience: extract entities and relations from agent reply."""
        entities = self.extract_entities(reply)
        relations = self.extract_relations(entities, reply)
        return entities, relations


def _eid(entity: dict) -> str:
    """Reconstruct entity id from type+name (same logic as _make_eid in world_model)."""
    slug = entity["name"].lower().replace(" ", "_").replace("-", "_")[:60]
    return f"{entity['type']}_{slug}"


# singleton
entity_extractor = EntityExtractor()
