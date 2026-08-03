"""Unit tests for ImportanceScorer and llm_helpers."""


from memory.importance_scorer import ImportanceFeatures, ImportanceScorer
from core.llm_helpers import build_context_text


class TestImportanceScorer:
    def test_default_score_low(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures()
        score = scorer.score(features)
        assert 0.0 <= score <= 0.2  # only source_reliability * 0.10

    def test_user_explicit_boost(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures(user_explicit=True)
        score = scorer.score(features)
        assert score >= 0.25

    def test_goal_related_boost(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures(goal_related=True, source_reliability=1.0)
        score = scorer.score(features)
        assert score >= 0.20

    def test_blocker_boost(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures(blocker_related=True)
        score = scorer.score(features)
        assert score >= 0.20

    def test_persona_boost(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures(persona_related=True)
        score = scorer.score(features)
        assert score >= 0.15

    def test_repeated_mentions_capped(self):
        scorer = ImportanceScorer()
        f_low = ImportanceFeatures(repeated_mentions=2)
        f_high = ImportanceFeatures(repeated_mentions=10)
        # 10 mentions should be capped to same as 5
        assert scorer.score(f_low) <= scorer.score(f_high)

    def test_all_features_max_score(self):
        scorer = ImportanceScorer()
        features = ImportanceFeatures(
            user_explicit=True,
            goal_related=True,
            blocker_related=True,
            persona_related=True,
            repeated_mentions=5,
            source_reliability=1.0,
            emotional_intensity=1.0,
            self_model_delta=1.0,
            prediction_error=1.0,
        )
        score = scorer.score(features)
        assert 0.8 <= score <= 1.5  # realistic max

    def test_self_model_delta_clamped(self):
        scorer = ImportanceScorer()
        f1 = ImportanceFeatures(self_model_delta=0.5)
        f2 = ImportanceFeatures(self_model_delta=2.0)  # clamped to 1.0
        # Both should contribute, but 2.0 clamped to 1.0
        assert scorer.score(f2) >= scorer.score(f1)

    def test_prediction_error_clamped(self):
        scorer = ImportanceScorer()
        f1 = ImportanceFeatures(prediction_error=-0.5)  # clamped to 0
        f2 = ImportanceFeatures(prediction_error=0.5)
        assert scorer.score(f2) >= scorer.score(f1)


class TestBuildContextText:
    def test_none_returns_empty(self):
        assert build_context_text(None) == ""

    def test_empty_dict_returns_empty(self):
        assert build_context_text({}) == ""

    def test_with_tasks_only(self):
        ctx = {"active_tasks": [{"name": "Fix bug"}, {"name": "Deploy"}]}
        result = build_context_text(ctx)
        assert "Active tasks" in result
        assert "Fix bug" in result

    def test_with_memories_only(self):
        ctx = {"memories": [{"content": "User prefers dark mode"}]}
        result = build_context_text(ctx)
        assert "Recent context" in result
        assert "dark mode" in result

    def test_with_both(self):
        ctx = {
            "active_tasks": [{"name": "Fix bug"}],
            "memories": [{"content": "Login page broken"}],
        }
        result = build_context_text(ctx)
        assert "Active tasks" in result
        assert "Recent context" in result

    def test_tasks_capped_at_3(self):
        ctx = {"active_tasks": [
            {"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"},
        ]}
        result = build_context_text(ctx)
        assert "D" not in result  # only first 3

    def test_memories_capped_at_2(self):
        ctx = {"memories": [
            {"content": "A"}, {"content": "B"}, {"content": "C"},
        ]}
        result = build_context_text(ctx)
        assert "C" not in result  # only first 2

    def test_memory_content_truncated_at_80(self):
        ctx = {"memories": [{"content": "x" * 100}]}
        result = build_context_text(ctx)
        assert len(result) < 200  # 80 chars + prefix
