"""Entity and relation extraction from agent replies.

Supports two extraction strategies:
1. LLM-based (primary, when LLM is available): uses the configured LLM
   to extract structured entities and relations with higher accuracy.
2. Rule-based (fallback): parses both raw format (/task:, TODO:) and
   LLM-structured output (bullet lists, numbered steps, priority markers,
   deadlines). Used when no LLM API key is configured.

Used by the cognition loop to auto-populate the WorldModelGraph.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("eva.entity_extractor")


# ── LLM-based extraction prompt ─────────────────────────────

ENTITY_EXTRACTION_PROMPT = """Extract entities and relations from the following text.

Return a JSON object with "entities" and "relations" arrays.

Entity types: task, person, file, project, risk, blocker
Each entity: {{"type": "...", "name": "...", "properties": {{"status": "active|completed", "priority": "low|medium|high|urgent", "deadline": "..."}}}}

Relations: {{"source": "entity_name", "target": "entity_name", "relation": "created|assigned_to|depends_on|blocks|references", "weight": 0.5-1.0}}

Text:
{text}

JSON:"""


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
    """Extract typed entities and relations from text.

    Supports LLM-based extraction (primary) with rule-based fallback.
    The LLM path is automatically used when an LLM adapter is available
    and produces valid JSON; otherwise falls back to regex patterns.
    """

    def __init__(self, llm: Any = None) -> None:
        """Initialize with optional LLM adapter for enhanced extraction.

        Args:
            llm: An LLMAdapter instance (from core.llm_adapter).
                 If None, only rule-based extraction is used.
        """
        self._llm = llm

    def extract_entities(self, text: str) -> list[dict[str, Any]]:
        """Extract entities from text.

        Tries LLM-based extraction first, falls back to rule-based.
        Entities include a confidence score (0.0-1.0) indicating extraction quality.
        """
        # Try LLM extraction first
        if self._llm is not None:
            try:
                llm_entities, _ = self._extract_via_llm(text)
                if llm_entities:
                    return self._deduplicate(llm_entities)
            except Exception:
                logger.debug("LLM entity extraction failed, falling back to rules", exc_info=True)

        # Rule-based fallback
        entities = self._extract_entities_rules(text)
        return self._deduplicate(entities)

    def extract_relations(
        self, entities: list[dict[str, Any]], text: str
    ) -> list[dict[str, Any]]:
        """Extract relations between entities.

        Tries LLM-based extraction first, falls back to rule-based.
        """
        if self._llm is not None:
            try:
                _, llm_relations = self._extract_via_llm(text)
                if llm_relations:
                    return llm_relations
            except Exception as e:
                logger.debug("LLM relation extraction failed: %s", e)

        return self._extract_relations_rules(entities, text)

    def extract_from_reply(
        self, reply: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Extract both entities and relations from a reply.

        Uses LLM when available for higher accuracy, with rule-based fallback.
        """
        # Try LLM path
        if self._llm is not None:
            try:
                entities, relations = self._extract_via_llm(reply)
                if entities:
                    return entities, relations
            except Exception:
                logger.debug("LLM extraction failed, using rules", exc_info=True)

        # Rule-based fallback
        entities = self._extract_entities_rules(reply)
        relations = self._extract_relations_rules(entities, reply)
        return entities, relations

    # ── LLM-based extraction ─────────────────────────────────

    def _extract_via_llm(self, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Use LLM to extract entities and relations as structured JSON."""
        if self._llm is None:
            return [], []

        prompt = ENTITY_EXTRACTION_PROMPT.format(text=text[:3000])
        messages = [
            {"role": "user", "content": prompt},
        ]
        response = self._llm.chat(messages)

        # Extract JSON from response (may have markdown fences)
        json_match = re.search(r'\{[\s\S]*"entities"[\s\S]*"relations"[\s\S]*\}', response)
        if not json_match:
            logger.debug("LLM extraction: no valid JSON found in response")
            return [], []

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.debug("LLM extraction: JSON parse failed")
            return [], []

        entities: list[dict[str, Any]] = []
        for e in data.get("entities", []):
            if isinstance(e, dict) and "type" in e and "name" in e:
                etype = e["type"]
                if etype not in ("task", "person", "file", "project", "risk", "blocker"):
                    etype = "task"
                props = e.get("properties", {}) if isinstance(e.get("properties"), dict) else {}
                confidence = float(e.get("confidence", 0.85))
                entities.append({
                    "type": etype,
                    "name": str(e["name"])[:200],
                    "properties": {
                        "status": props.get("status", "active"),
                        "priority": props.get("priority", "medium"),
                        "deadline": props.get("deadline", ""),
                        "source": "llm_extraction",
                        "confidence": confidence,
                    },
                })

        relations: list[dict[str, Any]] = []
        for r in data.get("relations", []):
            if isinstance(r, dict) and "source" in r and "target" in r and "relation" in r:
                relations.append({
                    "source": str(r["source"]),
                    "target": str(r["target"]),
                    "relation": str(r["relation"]),
                    "weight": float(r.get("weight", 0.7)),
                    "confidence": float(r.get("confidence", 0.7)),
                })

        logger.info("LLM extraction: %d entities, %d relations", len(entities), len(relations))
        return entities, relations

    # ── Rule-based extraction ─────────────────────────────────

    def _extract_entities_rules(self, text: str) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        seen: set[str] = set()

        # explicit task markers
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
                            "confidence": 0.7,
                        },
                    })

        # bullet/numbered list items
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
                        "confidence": 0.6,
                    },
                })

        # action markers
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

        # risks
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

    def _extract_relations_rules(
        self, entities: list[dict[str, Any]], text: str
    ) -> list[dict[str, Any]]:
        if not entities or len(entities) < 2:
            return []

        relations: list[dict[str, Any]] = []

        # user -> task
        for e in entities:
            if e["type"] == "task":
                relations.append({
                    "source": "user",
                    "target": _eid(e),
                    "relation": "created",
                    "weight": 0.9,
                })

        # task->task dependencies
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

        # task->person
        for task in tasks:
            for person in [e for e in entities if e["type"] == "person"]:
                if person["name"].lower() in text.lower():
                    relations.append({
                        "source": _eid(task),
                        "target": _eid(person),
                        "relation": "assigned_to",
                        "weight": 0.7,
                    })

        # tasks in same reply -> related
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

    # ── helpers ─────────────────────────────────────────────

    @staticmethod
    def _deduplicate(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate entities, keeping the one with highest confidence.

        Two entities are duplicates only if they have the same type AND
        their names are identical or near-identical (same first 80% of chars).
        Short names (<10 chars) must match exactly to be merged.
        """
        if len(entities) <= 1:
            return entities

        result: list[dict[str, Any]] = []
        for entity in entities:
            etype = entity.get("type", "")
            ename = entity.get("name", "").strip()
            ename_lower = ename.lower()
            econfs = entity.get("properties", {})
            econf = float(econfs.get("confidence", 0.5))

            found = False
            for existing in result:
                if existing.get("type") != etype:
                    continue
                ex_name = existing.get("name", "").strip()
                ex_lower = ex_name.lower()

                # Exact match (case-insensitive) → keep higher confidence
                if ename_lower == ex_lower:
                    ex_conf = float(existing.get("properties", {}).get("confidence", 0.5))
                    if econf > ex_conf:
                        existing["name"] = ename  # keep original casing of higher-conf
                        existing["properties"]["confidence"] = econf
                    found = True
                    break

                # For short names (<10 chars), only exact match counts
                if len(ename_lower) < 10 or len(ex_lower) < 10:
                    continue

                # Near-duplicate: one contains the other AND the shorter is
                # at least 80% of the longer → likely same entity
                shorter = ename_lower if len(ename_lower) < len(ex_lower) else ex_lower
                longer = ex_lower if len(ename_lower) < len(ex_lower) else ename_lower
                if shorter in longer and len(shorter) / len(longer) >= 0.8:
                    avg_conf = round((econf + float(
                        existing.get("properties", {}).get("confidence", 0.5)
                    )) / 2, 2)
                    # Keep the longer (more descriptive) name
                    existing["name"] = ename if len(ename) > len(ex_name) else ex_name
                    existing["properties"]["confidence"] = avg_conf
                    found = True
                    break

            if not found:
                result.append(entity)

        return result

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
            r"^[a-z]{1,3}\b$",
            r"^\d+$",
            r"^[.,;:!?]+$",
            r"^\d+\s+to\s+",
            r"^\d+\.\d+",
            r"^v\d+\.\d+",
        ]
        for pat in noise_patterns:
            if re.match(pat, name, re.IGNORECASE):
                return True
        return False


def _eid(entity: dict[str, Any]) -> str:
    slug = entity["name"].lower().replace(" ", "_").replace("-", "_")[:60]
    return f"{entity['type']}_{slug}"


# Module-level singleton with lazy LLM initialization.
# When an API key is configured, the extractor auto-upgrades to LLM-based extraction.
entity_extractor = EntityExtractor()


def _lazy_init_llm() -> None:
    """Wire LLM into the singleton if one is available and not already set."""
    if entity_extractor._llm is not None:
        return
    try:
        from core.llm_adapter import get_llm
        llm = get_llm()
        # Only set if it's not a MockLLM (mock means no real LLM available)
        from core.llm_adapter import MockLLM
        if not isinstance(llm, MockLLM):
            entity_extractor._llm = llm
            logger.info("entity_extractor upgraded to LLM-based extraction")
    except Exception as e:
        logger.debug("entity_extractor LLM init skipped: %s", e)
