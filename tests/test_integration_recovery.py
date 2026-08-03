"""Integration, recovery flow, and multi-agent tests."""

import tempfile
import os
from pathlib import Path

from world.world_model import WorldModelGraph
from core.policy_engine import PolicyEngine, State
from core.prediction import PredictionTracker
from core.entity_extractor import entity_extractor
from persona.self_model_store import SelfModelStore
from runtime.diagnostics import SystemDiagnostic, RecoveryActions
from app.bootstrap import bootstrap_system


# ── Quarantine → Recovery Flow ─────────────────────────────

class TestQuarantineRecoveryFlow:
    def test_dormant_to_command_to_dormant(self):
        pe = PolicyEngine()
        assert pe.state_machine.current == State.DORMANT
        pe.transition("user_command")
        assert pe.state_machine.current == State.COMMANDED
        pe.transition("command_completed")
        assert pe.state_machine.current == State.DORMANT

    def test_quarantine_recovery_cycle(self):
        pe = PolicyEngine()
        # normal operation
        pe.transition("user_command")
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict.value == "allow"

        # enter quarantine
        pe.transition("violation_detected")
        assert pe.state_machine.current == State.QUARANTINED
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict.value == "deny"

        # manual recovery
        recovery = RecoveryActions()
        result = recovery.reset_policy(pe)
        assert result.passed
        assert pe.state_machine.current == State.DORMANT

        # back to normal
        d = pe.evaluate("user_message", {"source": "user"})
        assert d.verdict.value == "allow"

    def test_policy_engine_get_state_after_cycles(self):
        pe = PolicyEngine()
        for _ in range(3):
            pe.transition("user_command")
            pe.transition("command_completed")
        state = pe.get_state()
        assert state["state_machine"]["history_size"] == 6

    def test_policy_state_in_diagnostic(self):
        pe = PolicyEngine()
        pe.transition("violation_detected")
        state = {"pending_events": 0, "pending_results": 0, "policy_state": pe.get_state()}
        diag = SystemDiagnostic()
        check = diag.check_runtime(state)
        assert not check.passed
        assert "quarantine" in str(check.detail).lower()


# ── Self-Model Recovery Persistence ────────────────────────

class TestSelfModelRecovery:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.path = Path(os.path.join(cls.tmpdir, "self_model.json"))

    def test_record_and_persist(self):
        store = SelfModelStore(self.path)
        sm = store.load_or_init()
        assert sm["_version"] == 2

        # use a fresh copy from defaults to avoid pollution from prior tests
        sm = {
            "identity": "test",
            "state_history": [],
            "perturbations": [],
            "prediction_errors": [],
            "stability_metrics": {"total_perturbations": 0, "mean_prediction_error": 0.0},
            "_version": 2,
        }
        store.record_state_change(sm, change_type="test", detail="test", loop_id="l1")
        store.record_prediction_error(sm, error=0.3, focus="test")
        store.record_perturbation(sm, cause="test", delta_magnitude=0.5, affected_fields=["test"])

        store.save(sm)
        assert self.path.exists()

        store2 = SelfModelStore(self.path)
        sm2 = store2.load_or_init()
        assert len(sm2["state_history"]) >= 1
        assert len(sm2["prediction_errors"]) >= 1
        assert len(sm2["perturbations"]) >= 1


# ── Multi-Agent Concurrent Execution ───────────────────────

class TestMultiAgentConcurrent:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()

    def test_multiple_agents_in_sequence(self):
        """Simulate a full cognition cycle with all 4 agents."""
        from agents.chat_agent import ChatAgent
        from agents.search_agent import SearchAgent
        from agents.coding_agent import CodingAgent
        from agents.docs_agent import DocsAgent
        from agents.base_agent import AgentTask

        agents = {
            "chat": ChatAgent(),
            "search": SearchAgent(),
            "coding": CodingAgent(),
            "docs": DocsAgent(),
        }

        tasks = [
            ("chat", AgentTask(kind="chat", payload={"text": "hello"})),
            ("search", AgentTask(kind="search", payload={"query": "def", "root": self.tmpdir})),
            ("coding", AgentTask(kind="code", payload={"text": "def foo(): pass"})),
            ("docs", AgentTask(kind="summarize", payload={"text": "EVA architecture doc v2"})),
        ]

        results = {}
        for name, task in tasks:
            agent = agents[name]
            if agent.can_handle(task):
                results[name] = agent.run(task)

        assert all(r.ok for r in results.values())
        assert len(results) == 4

    def test_world_model_accumulates_from_multi_agent(self):
        wm = WorldModelGraph()
        replies = [
            "[chat_agent] /task: implement login page /task: add error handling",
            "[coding_agent] reviewed app/main.py — need to /task: fix startup timeout",
        ]
        for reply in replies:
            entities, relations = entity_extractor.extract_from_reply(reply)
            for e in entities:
                wm.upsert_entity(e["type"], e["name"], e.get("properties", {}))
            for r in relations:
                wm.link(r["source"], r["target"], r["relation"], weight=r.get("weight", 1.0))

        tasks = wm.active_tasks
        assert len(tasks) >= 3
        names = [t["name"] for t in tasks]
        assert any("login" in n.lower() for n in names)
        assert any("error" in n.lower() for n in names)
        assert any("startup" in n.lower() for n in names)


# ── Bootstrap Recovery Flow ────────────────────────────────

class TestBootstrapRecovery:
    def test_bootstrap_shutdown_no_crash(self):
        c = bootstrap_system()
        from app.bootstrap import shutdown_system
        shutdown_system(c)
        assert not c.system_state["ready"]
        assert getattr(c.store._local, "conn", None) is None

    def test_double_bootstrap_no_crash(self):
        c1 = bootstrap_system()
        from app.bootstrap import shutdown_system
        shutdown_system(c1)
        c2 = bootstrap_system()
        assert c2.system_state["ready"]
        shutdown_system(c2)

    def test_diagnostic_after_bootstrap(self):
        c = bootstrap_system()
        diag = c.diagnostic
        assert diag.score >= 80
        assert diag.overall in ("healthy", "degraded")
        from app.bootstrap import shutdown_system
        shutdown_system(c)


# ── Prediction Tracker Recovery ─────────────────────────────

class TestPredictionRecovery:
    def test_empty_tracker_returns_zero(self):
        pt = PredictionTracker()
        assert pt.weighted_error() == 0.0
        assert pt.recent_error() == 0.0

    def test_error_decay(self):
        pt = PredictionTracker(decay_lambda=0.5)
        # errors on different content — decay weights older errors less
        for i in range(5):
            pt.record("focus", f"expected{i}", "actual")
        weighted = pt.weighted_error()
        assert weighted > 0
        assert weighted <= 1.0  # ranged 0-1
        # recent_error on same inputs should be high
        assert pt.recent_error() > 0

    def test_max_history(self):
        pt = PredictionTracker(max_history=10)
        for i in range(20):
            pt.record("focus", f"exp{i}", f"act{i}")
        assert len(pt.history) == 10

    def test_to_dict(self):
        pt = PredictionTracker()
        pt.record("focus_a", "exp", "act")
        d = pt.to_dict()
        assert "weighted_error" in d
        assert "recent_error" in d
        assert d["last_focus"] == "focus_a"
