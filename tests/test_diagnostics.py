"""System diagnostic and recovery unit tests."""

import tempfile
import os
import json
from pathlib import Path

from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from runtime.diagnostics import (
    SystemDiagnostic,
    DiagnosticCheck,
    DiagnosticReport,
    RecoveryActions,
)
from core.policy_engine import PolicyEngine


# ── Diagnostic Checks ──────────────────────────────────────

class TestSystemDiagnostic:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_db_check_healthy(self):
        diag = SystemDiagnostic()
        check = diag.check_db(self.store)
        assert check.passed
        assert "integrity OK" in check.detail

    def test_config_check_found(self):
        diag = SystemDiagnostic()
        check = diag.check_config()
        # should find at least constitution.yaml and config/*.yaml
        assert check.passed
        assert len(check.metadata.get("found", [])) >= 1

    def test_snapshot_missing_not_critical(self):
        diag = SystemDiagnostic()
        check = diag.check_snapshot(Path("/nonexistent/snapshot.json"))
        assert check.passed  # not critical — expected on first boot

    def test_snapshot_valid(self):
        tmp = os.path.join(self.tmpdir, "valid_snapshot.json")
        with open(tmp, "w") as f:
            json.dump({"version": "0.1", "world_model": {"focus": "test"}}, f)
        diag = SystemDiagnostic()
        check = diag.check_snapshot(Path(tmp))
        assert check.passed

    def test_snapshot_corrupt(self):
        tmp = os.path.join(self.tmpdir, "corrupt.json")
        with open(tmp, "wb") as f:
            f.write(b"\x00\x01NOT JSON")
        diag = SystemDiagnostic()
        check = diag.check_snapshot(Path(tmp))
        assert not check.passed

    def test_memory_tiers_check(self):
        diag = SystemDiagnostic()
        tm = TieredMemoryManager(self.store)
        tm.ingest("test memory", importance=0.9, source="test")
        check = diag.check_memory_tiers(tm)
        assert check.passed
        assert "S1=" in check.detail

    def test_runtime_healthy(self):
        diag = SystemDiagnostic()
        state = {
            "pending_events": 0, "pending_results": 0,
            "policy_state": {"state_machine": {"current": "dormant"}},
        }
        check = diag.check_runtime(state)
        assert check.passed

    def test_runtime_degraded_on_quarantine(self):
        diag = SystemDiagnostic()
        state = {
            "pending_events": 0, "pending_results": 0,
            "policy_state": {"state_machine": {"current": "quarantined"}},
        }
        check = diag.check_runtime(state)
        assert not check.passed
        assert "quarantine" in " ".join(check.detail) if isinstance(check.detail, list) else check.detail

    def test_full_diagnostic_scoring(self):
        diag = SystemDiagnostic()
        tm = TieredMemoryManager(self.store)
        state = {
            "pending_events": 0, "pending_results": 0,
            "policy_state": {"state_machine": {"current": "dormant"}},
        }
        report = diag.run_full(self.store, tm, state)
        assert report.score >= 66
        # degraded is expected in test env without LLM API keys
        assert report.overall in ("healthy", "degraded")
        assert len(report.checks) >= 4
        assert report.to_dict()["score"] == report.score


# ── Recovery Actions ────────────────────────────────────────

class TestRecoveryActions:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = SQLiteStore(Path(os.path.join(cls.tmpdir, "test.db")))
        cls.store.init_db()

    def test_recover_db(self):
        result = RecoveryActions.recover_db(self.store)
        assert result.passed
        assert "re-initialized" in result.detail

    def test_reset_policy(self):
        pe = PolicyEngine()
        pe.transition("violation_detected")
        assert pe.state_machine.current.value == "quarantined"

        result = RecoveryActions.reset_policy(pe)
        assert result.passed
        assert pe.state_machine.current.value == "dormant"

    def test_reset_policy_none(self):
        result = RecoveryActions.reset_policy(None)
        assert not result.passed

    def test_recover_snapshot(self):
        tmp = os.path.join(self.tmpdir, "recovery_snap.json")
        result = RecoveryActions.recover_snapshot(Path(tmp))
        assert result.passed
        assert os.path.exists(tmp)

    def test_clear_registry(self):
        import time
        from runtime.result_registry import ResultRegistry
        rr = ResultRegistry()
        rr.create("task-1")
        rr.create("task-2")
        assert rr.size() == 2

        # entries are fresh — cleanup(ttl=0) won't clear them immediately
        # but the diagnostic still reports success (operation ran)
        result = RecoveryActions.clear_stale_registry(rr)
        assert result.passed
        assert "cleared" in result.detail
        # fresh entries remain; stale entries (if any) were removed
        assert rr.size() == 2


# ── Diagnostic Report ───────────────────────────────────────

class TestDiagnosticReport:
    def test_all_passed_healthy(self):
        checks = [
            DiagnosticCheck(name="a", passed=True, detail="ok"),
            DiagnosticCheck(name="b", passed=True, detail="ok"),
        ]
        report = DiagnosticReport(checks=checks, score=100, overall="healthy")
        assert len(report.failed_checks()) == 0
        d = report.to_dict()
        assert d["overall"] == "healthy"
        assert d["failed_count"] == 0

    def test_mixed_degraded(self):
        checks = [
            DiagnosticCheck(name="a", passed=True, detail="ok"),
            DiagnosticCheck(name="b", passed=False, detail="bad", recommendation="fix"),
        ]
        report = DiagnosticReport(checks=checks, score=50, overall="degraded")
        assert len(report.failed_checks()) == 1
        d = report.to_dict()
        assert d["failed_count"] == 1
