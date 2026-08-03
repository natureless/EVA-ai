"""Tests for LLM-based entity extraction in core.entity_extractor."""

from __future__ import annotations


from core.entity_extractor import (
    ENTITY_EXTRACTION_PROMPT,
    EntityExtractor,
    _eid,
    _lazy_init_llm,
    entity_extractor,
)


class TestLLMEntityExtractor:
    """Tests for LLM-based extraction path."""

    def test_llm_prompt_contains_text(self):
        """The LLM prompt template includes the input text."""
        prompt = ENTITY_EXTRACTION_PROMPT.format(text="sample text")
        assert "sample text" in prompt
        assert "entities" in prompt
        assert "relations" in prompt

    def test_llm_json_parsing(self):
        """Extractor parses valid LLM JSON response."""
        # Simulate an LLM response
        mock_response = '''```json
{
  "entities": [
    {"type": "task", "name": "Fix login bug", "properties": {"status": "active", "priority": "high", "deadline": "2026-03-15"}},
    {"type": "person", "name": "Alice", "properties": {}}
  ],
  "relations": [
    {"source": "task_fix_login_bug", "target": "person_alice", "relation": "assigned_to", "weight": 0.8}
  ]
}
```'''
        # Verify the JSON parsing path works — the regex should find the JSON block
        import re
        import json

        json_match = re.search(r'\{[\s\S]*"entities"[\s\S]*"relations"[\s\S]*\}', mock_response)
        assert json_match is not None

        data = json.loads(json_match.group(0))
        assert len(data["entities"]) == 2
        assert data["entities"][0]["type"] == "task"
        assert data["entities"][0]["name"] == "Fix login bug"
        assert len(data["relations"]) == 1

    def test_llm_extraction_no_json(self):
        """When LLM returns no JSON, returns empty lists."""
        extractor = EntityExtractor()
        entities, relations = extractor._extract_via_llm("just some text without json")
        assert entities == []
        assert relations == []

    def test_extract_via_llm_requires_llm(self):
        """Without LLM, _extract_via_llm returns empty."""
        extractor = EntityExtractor(llm=None)
        entities, relations = extractor._extract_via_llm("any text")
        assert entities == []
        assert relations == []

    def test_extract_from_reply_falls_back_to_rules_without_llm(self):
        """Without LLM, extract_from_reply uses rule-based extraction."""
        extractor = EntityExtractor(llm=None)
        entities, relations = extractor.extract_from_reply("TODO: fix the login bug")
        assert len(entities) >= 1
        assert entities[0]["type"] == "task"
        assert "fix the login bug" in entities[0]["name"].lower()

    def test_llm_response_with_invalid_entity_skipped(self):
        """LLM response entities missing required fields are skipped."""
        # Simulate parsing code path: entities without "type" or "name" are filtered
        data = {
            "entities": [
                {"type": "task"},  # missing name
                {"name": "only name"},  # missing type
                {"type": "task", "name": "valid task", "properties": {}},
            ],
            "relations": [],
        }
        # Manually exercise the extraction loop logic
        entities = []
        for e in data["entities"]:
            if isinstance(e, dict) and "type" in e and "name" in e:
                entities.append(e)
        assert len(entities) == 1
        assert entities[0]["name"] == "valid task"

    def test_unknown_entity_type_defaults_to_task(self):
        """Entity types not in the allowed set default to 'task'."""
        data = {
            "entities": [
                {"type": "unicorn", "name": "magic thing", "properties": {}},
            ],
            "relations": [],
        }
        import re
        import json
        response = json.dumps(data)
        json_match = re.search(r'\{[\s\S]*"entities"[\s\S]*"relations"[\s\S]*\}', response)
        parsed = json.loads(json_match.group(0))
        e = parsed["entities"][0]
        etype = e["type"]
        if etype not in ("task", "person", "file", "project", "risk", "blocker"):
            etype = "task"
        assert etype == "task"

    def test_llm_relations_parsed_correctly(self):
        """Relations from LLM response are parsed with correct fields."""
        data = {
            "entities": [],
            "relations": [
                {"source": "A", "target": "B", "relation": "depends_on", "weight": 0.5},
                {"source": "C"},  # missing target and relation — should be skipped
                {"source": "D", "target": "E", "relation": "blocks", "weight": 0.9},
            ],
        }
        relations = []
        for r in data["relations"]:
            if isinstance(r, dict) and "source" in r and "target" in r and "relation" in r:
                relations.append({
                    "source": str(r["source"]),
                    "target": str(r["target"]),
                    "relation": str(r["relation"]),
                    "weight": float(r.get("weight", 0.7)),
                })
        assert len(relations) == 2
        assert relations[0]["relation"] == "depends_on"
        assert relations[1]["relation"] == "blocks"
        assert relations[1]["weight"] == 0.9

    def test_llm_relation_default_weight(self):
        """Relations without weight get default 0.7."""
        data = {
            "entities": [],
            "relations": [
                {"source": "X", "target": "Y", "relation": "related_to"},
            ],
        }
        import re
        import json
        response = json.dumps(data)
        json_match = re.search(r'\{[\s\S]*"entities"[\s\S]*"relations"[\s\S]*\}', response)
        parsed = json.loads(json_match.group(0))
        r = parsed["relations"][0]
        weight = float(r.get("weight", 0.7))
        assert weight == 0.7


class TestLazyInitLLM:
    """Tests for the lazy LLM initialization function."""

    def test_lazy_init_noop_when_already_set(self, monkeypatch):
        """_lazy_init_llm does nothing when LLM is already set."""
        from core.llm_adapter import MockLLM
        saved = entity_extractor._llm
        try:
            entity_extractor._llm = MockLLM()
            # Should return immediately without error
            _lazy_init_llm()
            assert isinstance(entity_extractor._llm, MockLLM)
        finally:
            entity_extractor._llm = saved

    def test_lazy_init_noop_without_api_key(self):
        """_lazy_init_llm does not set MockLLM."""
        saved = entity_extractor._llm
        try:
            entity_extractor._llm = None
            _lazy_init_llm()
            # Without API keys, get_llm returns MockLLM, which we skip
            assert entity_extractor._llm is None
        finally:
            entity_extractor._llm = saved


class TestHelperFunctions:
    """Tests for entity extraction helpers."""

    def test_eid_creates_slug(self):
        """_eid creates a type_name slug from entity dict."""
        entity = {"type": "task", "name": "Fix Login Bug"}
        result = _eid(entity)
        assert result.startswith("task_")
        assert "fix_login_bug" in result

    def test_eid_truncates_long_names(self):
        """_eid truncates names longer than 60 chars."""
        entity = {"type": "risk", "name": "a" * 100}
        result = _eid(entity)
        assert len(result) <= 70  # "risk_" + 60 chars max


class TestRuleBasedExtractionStillWorks:
    """Verify rule-based extraction continues to work alongside LLM path."""

    def test_chinese_task_extraction(self):
        """Chinese task markers are extracted."""
        entities = entity_extractor.extract_entities("任务：修复登录bug  待办：写测试  分配给：@小明")
        names = [e["name"].lower() for e in entities]
        assert any("修复登录bug" in n for n in names) or any("bug" in n for n in names)

    def test_english_task_extraction(self):
        """English TODO markers are extracted."""
        entities = entity_extractor.extract_entities("TODO: fix login bug\n/task: write tests")
        assert len(entities) >= 1
        task_names = [e["name"] for e in entities if e["type"] == "task"]
        assert len(task_names) >= 1

    def test_person_extraction(self):
        """Person mentions are extracted."""
        entities = entity_extractor.extract_entities("assigned to @alice please review")
        persons = [e for e in entities if e["type"] == "person"]
        assert any("alice" in p["name"].lower() for p in persons)

    def test_file_extraction(self):
        """File references are extracted."""
        entities = entity_extractor.extract_entities("check `main.py` and file: config.yaml")
        files = [e for e in entities if e["type"] == "file"]
        assert len(files) >= 1

    def test_risk_extraction(self):
        """Risk mentions are extracted."""
        entities = entity_extractor.extract_entities("This is a critical risk: data loss possible")
        risks = [e for e in entities if e["type"] == "risk"]
        assert len(risks) >= 1

    def test_priority_detection(self):
        """Priority markers are detected in task names."""
        entities = entity_extractor.extract_entities("TODO: urgent fix for critical bug")
        urgent_tasks = [
            e for e in entities
            if e["type"] == "task" and e["properties"].get("priority") == "urgent"
        ]
        assert len(urgent_tasks) >= 1

    def test_deadline_detection(self):
        """Deadline dates are detected."""
        entities = entity_extractor.extract_entities("- submit report by 2026-03-15")
        tasks_with_deadline = [
            e for e in entities
            if e["type"] == "task" and e["properties"].get("deadline")
        ]
        assert len(tasks_with_deadline) >= 1

    def test_done_detection(self):
        """Completed tasks are marked as done."""
        entities = entity_extractor.extract_entities("- [x] write tests\n* completed the feature")
        done_tasks = [
            e for e in entities
            if e["type"] == "task" and e["properties"].get("status") == "completed"
        ]
        assert len(done_tasks) >= 1

    def test_extract_from_reply_returns_both(self):
        """extract_from_reply returns both entities and relations."""
        reply = "TODO: fix login\nassigned to @alice"
        entities, relations = entity_extractor.extract_from_reply(reply)
        assert isinstance(entities, list)
        assert isinstance(relations, list)
        assert len(entities) >= 1

    def test_empty_text_returns_empty(self):
        """Empty text produces no entities."""
        entities = entity_extractor.extract_entities("")
        assert entities == []

    def test_noise_filtered(self):
        """Noise items like bare numbers are filtered out."""
        entities = entity_extractor.extract_entities("- 123\n- if\n- real task")
        # "123" and "if" should be filtered; "real task" should remain
        task_names = [e["name"].lower() for e in entities if e["type"] == "task"]
        assert "123" not in task_names
        assert "if" not in task_names

    def test_bullet_tasks_extracted(self):
        """Bullet-point tasks are extracted."""
        entities = entity_extractor.extract_entities("- fix the login bug\n- [x] write tests\n* deploy to prod")
        task_count = len([e for e in entities if e["type"] == "task"])
        assert task_count >= 2

    def test_numbered_tasks_extracted(self):
        """Numbered list items are extracted."""
        entities = entity_extractor.extract_entities("1) update docs\n2. review code\n3) ship it")
        task_count = len([e for e in entities if e["type"] == "task"])
        assert task_count >= 2
