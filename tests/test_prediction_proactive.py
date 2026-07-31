"""Unit tests for PredictionTracker and ProactiveEngine."""

import time

import pytest

from core.prediction import PredictionTracker, PredictionRecord
from core.proactive_engine import ProactiveEngine, ProactiveDecision


class TestPredictionTracker:
    def test_record_returns_record(self):
        pt = PredictionTracker()
        rec = pt.record("focus1", "expected outcome", "actual outcome")
        assert isinstance(rec, PredictionRecord)
        assert rec.focus == "focus1"

    def test_weighted_error_empty(self):
        pt = PredictionTracker()
        assert pt.weighted_error() == 0.0

    def test_weighted_error_with_records(self):
        pt = PredictionTracker()
        pt.record("f", "hello world", "hello world")  # error=0
        pt.record("f", "hello world", "goodbye")      # error>0
        err = pt.weighted_error()
        assert 0.0 < err < 1.0

    def test_recent_error_window(self):
        pt = PredictionTracker()
        for i in range(10):
            pt.record("f", f"expected {i}", f"actual {i}")
        err = pt.recent_error(window=3)
        assert 0.0 <= err <= 1.0

    def test_history_capped(self):
        pt = PredictionTracker(max_history=10)
        for i in range(20):
            pt.record("f", f"e{i}", f"a{i}")
        assert len(pt.history) == 10

    def test_to_dict(self):
        pt = PredictionTracker()
        pt.record("focus", "expected", "actual")
        d = pt.to_dict()
        assert "weighted_error" in d
        assert "recent_error" in d
        assert d["last_focus"] == "focus"

    def test_compute_error_exact_match(self):
        assert PredictionTracker._compute_error("hello", "hello") == 0.0

    def test_compute_error_completely_different(self):
        err = PredictionTracker._compute_error("hello world", "goodbye")
        assert err > 0.5

    def test_compute_error_one_empty(self):
        assert PredictionTracker._compute_error("hello", "") == 1.0

    def test_compute_error_both_empty(self):
        assert PredictionTracker._compute_error("", "") == 0.0

    def test_decay_weight_newer_higher(self):
        pt = PredictionTracker(decay_lambda=0.5)
        w0 = pt._decay_weight(0)  # newest
        w5 = pt._decay_weight(5)  # older
        assert w0 > w5


class TestProactiveEngine:
    def test_no_focus_no_trigger(self):
        engine = ProactiveEngine()
        decision = engine.evaluate_stagnation(
            world_model={"focus": ""},
            proactive_state={},
            now_ts=time.time(),
        )
        assert decision.should_trigger is False
        assert decision.reason == "no_focus"

    def test_idle_focus_no_trigger(self):
        engine = ProactiveEngine()
        decision = engine.evaluate_stagnation(
            world_model={"focus": "idle"},
            proactive_state={},
            now_ts=time.time(),
        )
        assert decision.should_trigger is False

    def test_no_baseline_no_trigger(self):
        engine = ProactiveEngine()
        decision = engine.evaluate_stagnation(
            world_model={"focus": "working"},
            proactive_state={},
            now_ts=time.time(),
        )
        assert decision.reason == "no_user_activity_baseline"

    def test_below_threshold_no_trigger(self):
        engine = ProactiveEngine(stagnation_threshold_sec=86400)
        now = time.time()
        decision = engine.evaluate_stagnation(
            world_model={"focus": "working"},
            proactive_state={"last_user_message_ts": now - 60},  # 1 min ago
            now_ts=now,
        )
        assert decision.should_trigger is False
        assert decision.reason == "below_threshold"

    def test_stagnation_detected_triggers(self):
        engine = ProactiveEngine(stagnation_threshold_sec=10, reminder_cooldown_sec=5)
        now = time.time()
        decision = engine.evaluate_stagnation(
            world_model={"focus": "working"},
            proactive_state={"last_user_message_ts": now - 60},  # 60s idle
            now_ts=now,
        )
        assert decision.should_trigger is True
        assert decision.reason == "stagnation_detected"

    def test_cooldown_prevents_trigger(self):
        engine = ProactiveEngine(stagnation_threshold_sec=10, reminder_cooldown_sec=3600)
        now = time.time()
        decision = engine.evaluate_stagnation(
            world_model={"focus": "working"},
            proactive_state={
                "last_user_message_ts": now - 60,
                "last_reminder_ts": now - 30,  # recent reminder
            },
            now_ts=now,
        )
        assert decision.reason == "cooldown_active"

    def test_build_reminder_message(self):
        engine = ProactiveEngine()
        msg = engine.build_reminder_message(focus="debugging", idle_sec=7200)
        assert "debugging" in msg
        assert "2.0 hours" in msg
