"""Rule-based entity and relation extraction from agent replies.

Parses both raw format (/task:, TODO:) and LLM-structured output
(bullet lists, numbered steps, priority markers, deadlines).
Used by the cognition loop to auto-populate the WorldModelGraph.
"""

import re
from typing import Any


# ── Task extraction patterns ────────────────────────────────

TASK_PATTERNS = [
    re.compile(r"/task[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"TODO[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"任务[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"\[task\]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"待办[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
]

# LLM output patterns: bullet points, checkboxes, and numbered lists
BULLET_TASK = re.compile(
    r"^\s*[-*•]\s+(?:\[(?P<status> |x)\]\s+)?(?P<name>[^\n]+)",
    re.MULTILINE,
)
NUMBERED_TASK = re.compile(
    r"^\s*\d+[.)]\s+(?P<name>[^\n]+)",
    re.MULTILINE,
)

# Explicit action markers from LLM replies
ACTION_MARKERS = [
    re.compile(r"(?:建议|should|need to|must|recommend)\s+(.+?)(?:[.!。！\n]|$)", re.IGNORECASE),
    re.compile(r"(?:下一步|next step|action item)[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"\d+[.)]\s*\*{0,2}(.+?)\*{0,2}\s*(?:\n|$)", re.IGNORECASE),
]

# Priority markers
PRIORITY_MARKERS = {
    "urgent": "urgent",
    "紧急": "urgent",
    "high": "high",
    "高": "high",
    "medium": "medium",
    "中": "medium",
    "low": "low",
    "低": "low",
}

# Deadline patterns
DEADLINE_PATTERNS = [
    re.compile(r"(?:by|due|deadline|before|截止|在?之前)[:：]?\s*(.+?)(?:[.!。！\n]|$)", re.IGNORECASE),
    re.compile(r"(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"(?:in|within)\s+(\d+)\s*(day|week|hour|天|周|小时|分钟)", re.IGNORECASE),
]

# Status completion markers
DONE_MARKERS = ["done", "completed", "完成", "已完成", "finished", "已修复", "fixed"]

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
    """Rule-based extraction of typed entities from text.

    Enhanced for LLM output: parses bullet lists, numbered steps,
    priority markers, deadlines, and completion status.
    """

    def extract_entities(self, text: str) -> list[dict]:
        entities: list[dict] = []
        seen: set[str] = set()

        # ── explicit task markers ────────────────────────────
        for pattern in TASK_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip().rstrip(".!！。")
                key = f"task_{name}"
                if key not in seen and len(name) > 1:
                    seen.add(key)
                    entities.append({
                        "type": "task", "name": name,
                        "properties": {
                            "status": "active", "source": "agent_reply",
                            "priority": self._detect_priority(name),
                        },
                    })

        # ── bullet/numbered list items ───────────────────────
        for pattern in (BULLET_TASK, NUMBERED_TASK):
            for match in pattern.finditer(text):
                status = (match.groupdict().get("status") or "").strip()
                name = match.group("name").strip().rstrip(".!！。")
                key = f"task_{name}"
                if key in seen or len(name) < 3 or len(name) > 200:
                    continue
                if self._is_noise(name):
                    continue
                seen.add(key)
                entity_status = "completed" if status in ("x",) or self._is_done(name) else "active"
                entities.append({
                    "type": "task", "name": name,
                    "properties": {
                        "status": entity_status,
                        "source": "agent_reply",
                        "priority": self._detect_priority(name),
                        "deadline": self._detect_deadline(name),
                    },
                })

        # ── action markers ───────────────────────────────────
        for pattern in ACTION_MARKERS:
            for match in pattern.finditer(text):
                name = match.group(1).strip().rstrip(".!！。")
                key = f"task_{name}"
                if key not in seen and 3 < len(name) < 200 and not self._is_noise(name):
                    seen.add(key)
                    entities.append({
                        "type": "task", "name": name,
                        "properties": {
                            "status": "active", "source": "agent_reply",
                            "priority": self._detect_priority(name),
                        },
                    })

        # ── persons ──────────────────────────────────────────
        for pattern in PERSON_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                key = f"person_{name}"
                if key not in seen and len(name) > 1:
                    seen.add(key)
                    entities.append({
                        "type": "person", "name": name, "properties": {},
                    })

        # ── files ────────────────────────────────────────────
        for pattern in FILE_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                key = f"file_{name}"
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        "type": "file", "name": name, "properties": {},
                    })

        # ── risks ────────────────────────────────────────────
        for sentence in re.split(r"[.!！。\n]", text):
            s = sentence.strip().lower()
            if any(m in s for m in RISK_MARKERS) and len(s) > 3:
                key = f"risk_{s[:40]}"
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        "type": "risk", "name": sentence.strip()[:80],
                        "properties": {"detected_in": "agent_reply"},
                    })

        return entities

    def extract_relations(
        self, entities: list[dict], text: str
    ) -> list[dict]:
        if not entities or len(entities) < 2:
            return []

        relations: list[dict] = []

        # user → task
        for e in entities:
            if e["type"] == "task":
                relations.append({
                    "source": "user",
                    "target": _eid(e),
                    "relation": "created",
                    "weight": 0.9,
                })

        # task→task dependencies
        tasks = [e for e in entities if e["type"] == "task"]
        for i, t1 in enumerate(tasks):
            for t2 in tasks[i + 1:]:
                if any(m in text.lower() for m in DEPENDENCY_MARKERS):
                    if t1["name"].lower() in text.lower():
                        relations.append({
                            "source": _eid(t2),
                            "target": _eid(t1),
                            "relation": "depends_on",
                            "weight": 0.6,
                        })

        # task→person
        for task in tasks:
            for person in [e for e in entities if e["type"] == "person"]:
                if person["name"].lower() in text.lower():
                    relations.append({
                        "source": _eid(task),
                        "target": _eid(person),
                        "relation": "assigned_to",
                        "weight": 0.7,
                    })

        # tasks in same reply → related
        for i, t1 in enumerate(tasks):
            for t2 in tasks[i + 1:]:
                if not any(
                    r for r in relations
                    if (r["source"] == _eid(t1) and r["target"] == _eid(t2))
                    or (r["source"] == _eid(t2) and r["target"] == _eid(t1))
                ):
                    relations.append({
                        "source": _eid(t1),
                        "target": _eid(t2),
                        "relation": "related_to",
                        "weight": 0.3,
                    })

        return relations

    def extract_from_reply(self, reply: str) -> tuple[list[dict], list[dict]]:
        entities = self.extract_entities(reply)
        relations = self.extract_relations(entities, reply)
        return entities, relations

    # ── helpers ─────────────────────────────────────────────

    def _detect_priority(self, text: str) -> str:
        t = text.lower()
        for marker, level in PRIORITY_MARKERS.items():
            if marker in t:
                return level
        return "medium"

    def _detect_deadline(self, text: str) -> str:
        for pattern in DEADLINE_PATTERNS:
            m = pattern.search(text)
            if m:
                return m.group(1).strip()
        return ""

    def _is_done(self, text: str) -> bool:
        t = text.lower()
        return any(m in t for m in DONE_MARKERS)

    @staticmethod
    def _is_noise(name: str) -> bool:
        """Filter out bullet items that aren't tasks."""
        noise_patterns = [
            r"^(if|when|while|because|since|although|however|therefore|note|for example)\b",
            r"^[a-z]{1,3}\b$",        # single short word
            r"^\d+$",                   # pure number
            r"^[.,;:!?]+$",            # pure punctuation
            r"^\d+\s+to\s+",           # "0 to production" from "v2.0 to"
            r"^\d+\.\d+",              # version numbers like "2.0"
            r"^v\d+\.\d+",             # "v2.0"
        ]
        for pat in noise_patterns:
            if re.match(pat, name, re.IGNORECASE):
                return True
        return False


def _eid(entity: dict[str, Any]) -> str:
    slug = entity["name"].lower().replace(" ", "_").replace("-", "_")[:60]
    return f"{entity['type']}_{slug}"


entity_extractor = EntityExtractor()
