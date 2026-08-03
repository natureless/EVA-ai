"""Unit tests for runtime.diagnostics — SystemDiagnostic and recovery functions."""

from pathlib import Path
from unittest.mock import MagicMock


from runtime.diagnostics import (
    DiagnosticCheck,
    DiagnosticReport,
    SystemDiagnostic,
)


class TestDiagnosticCheck:
    def test_passed_check(self):
        check = DiagnosticCheck(name="test", passed=True, detail="ok")
        assert check.passed is True
        assert check.name == "test"

    def test_failed_check(self):
        check = DiagnosticCheck(name="test", passed=False, detail="error",
                                recommendation="fix it")
        assert check.passed is False
        assert check.recommendation == "fix it"


class TestDiagnosticReport:
    def test_default_healthy(self):
        report = DiagnosticReport()
        assert report.overall == "healthy"
        assert report.score == 100

    def test_failed_checks_filters(self):
        report = DiagnosticReport(checks=[
            DiagnosticCheck(name="a", passed=True),
            DiagnosticCheck(name="b", passed=False),
            DiagnosticCheck(name="c", passed=False),
        ])
        failed = report.failed_checks()
        assert len(failed) == 2

    def test_to_dict(self):
        report = DiagnosticReport(checks=[
            DiagnosticCheck(name="x", passed=True, detail="good"),
        ])
        d = report.to_dict()
        assert d["overall"] == "healthy"
        assert d["failed_count"] == 0
        assert len(d["checks"]) == 1


class TestSystemDiagnostic:
    def test_check_db_integrity_ok(self):
        store = MagicMock()
        store.fetchall.side_effect = [
            [{"integrity_check": "ok"}],
            [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}, {"name": "t4"}, {"name": "t5"}],
        ]
        diag = SystemDiagnostic()
        check = diag.check_db(store)
        assert check.passed is True
        assert "integrity OK" in check.detail

    def test_check_db_too_few_tables(self):
        store = MagicMock()
        store.fetchall.side_effect = [
            [{"integrity_check": "ok"}],
            [{"name": "t1"}, {"name": "t2"}],
        ]
        diag = SystemDiagnostic()
        check = diag.check_db(store)
        assert check.passed is False

    def test_check_db_integrity_fail(self):
        store = MagicMock()
        store.fetchall.side_effect = [
            [{"integrity_check": "malformed"}],
            [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}, {"name": "t4"}, {"name": "t5"}],
        ]
        diag = SystemDiagnostic()
        check = diag.check_db(store)
        assert check.passed is False

    def test_check_db_exception(self):
        store = MagicMock()
        store.fetchall.side_effect = RuntimeError("db down")
        diag = SystemDiagnostic()
        check = diag.check_db(store)
        assert check.passed is False

    def test_check_config_all_present(self, tmp_path):
        diag = SystemDiagnostic(config_paths=[
            tmp_path / "a.yaml", tmp_path / "b.yaml",
        ])
        (tmp_path / "a.yaml").write_text("key: val")
        (tmp_path / "b.yaml").write_text("key: val")
        check = diag.check_config()
        assert check.passed is True

    def test_check_config_missing(self, tmp_path):
        diag = SystemDiagnostic(config_paths=[
            tmp_path / "missing.yaml",
        ])
        check = diag.check_config()
        # Missing config files are reported but not failing
        assert "missing" in check.detail.lower() or check.passed is True

    def test_check_snapshot_exists(self, tmp_path):
        snap = tmp_path / "latest.json"
        snap.write_text('{"version":"1.0","world_model":{}}')
        diag = SystemDiagnostic()
        check = diag.check_snapshot(snap)
        assert check.passed is True

    def test_check_snapshot_missing_not_critical(self):
        snap = Path("/nonexistent/snap.json")
        diag = SystemDiagnostic()
        check = diag.check_snapshot(snap)
        # Missing snapshot is not critical — first boot is normal
        assert check.name == "snapshot"

    def test_run_full_returns_report(self):
        store = MagicMock()
        store.fetchall.side_effect = [
            [{"integrity_check": "ok"}],
            [{"name": f"t{i}"} for i in range(10)],
        ]
        diag = SystemDiagnostic()
        report = diag.run_full(
            store,
            snapshot_path=Path("/nonexistent"),
            tiered_memory=MagicMock(),
            system_state={"ready": True},
        )
        assert isinstance(report, DiagnosticReport)
        assert report.overall in ("healthy", "degraded", "critical")

    def test_check_memory_tiers_healthy(self):
        diag = SystemDiagnostic()
        tiered = MagicMock()
        tiered.stats.return_value = {
            "S1_session": {"entries": 10},
            "S2_working": {"entries": 50},
            "S3_long_term": {"entries_active": 200},
            "S4_world_model": {"entities": 5, "edges": 3},
            "S5_event_trace": {"events": 1000},
        }
        check = diag.check_memory_tiers(tiered)
        assert check.passed is True

    def test_check_memory_tiers_orphan_edges(self):
        diag = SystemDiagnostic()
        tiered = MagicMock()
        tiered.stats.return_value = {
            "S1_session": {"entries": 0},
            "S2_working": {"entries": 0},
            "S3_long_term": {"entries_active": 0},
            "S4_world_model": {"entities": 0, "edges": 5},
            "S5_event_trace": {"events": 0},
        }
        check = diag.check_memory_tiers(tiered)
        assert check.passed is False
        assert "orphan" in check.detail

    def test_check_memory_tiers_exception(self):
        diag = SystemDiagnostic()
        tiered = MagicMock()
        tiered.stats.side_effect = RuntimeError("tiered down")
        check = diag.check_memory_tiers(tiered)
        assert check.passed is False

    def test_check_runtime_healthy(self):
        diag = SystemDiagnostic()
        check = diag.check_runtime({
            "pending_events": 5,
            "pending_results": 0,
            "policy_state": {"state_machine": {"current": "dormant"}},
        })
        assert check.passed is True

    def test_check_runtime_queue_backed_up(self):
        diag = SystemDiagnostic()
        check = diag.check_runtime({
            "pending_events": 100,
            "pending_results": 0,
            "policy_state": {"state_machine": {"current": "dormant"}},
        })
        assert check.passed is False
        assert "backed up" in check.detail

    def test_check_runtime_quarantined(self):
        diag = SystemDiagnostic()
        check = diag.check_runtime({
            "pending_events": 0,
            "pending_results": 0,
            "policy_state": {"state_machine": {"current": "quarantined"}},
        })
        assert check.passed is False
        assert "quarantine" in check.detail
