"""Tests for MVSC API endpoints using the existing TestClient fixture."""

import sys

import pytest

sys.path.insert(0, ".")

from fastapi.testclient import TestClient


class TestMVSCAPIEndpoints:
    """Test MVSC API endpoints return correct responses.

    Uses the 'client' fixture from conftest.py which provides a
    fully bootstrapped TestClient with lifespan.
    """

    @pytest.fixture(autouse=True)
    def setup(self, client: TestClient):
        self.client = client

    def test_mvsc_state_endpoint(self):
        """GET /api/mvsc/state should return ConsciousState view."""
        r = self.client.get("/api/mvsc/state")
        assert r.status_code == 200
        data = r.json()
        assert "subject_id" in data
        assert "runtime_mode" in data
        assert "world" in data

    def test_mvsc_ablation_endpoint(self):
        """GET /api/mvsc/ablation should return feature flags."""
        r = self.client.get("/api/mvsc/ablation")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_mvsc_metrics_endpoint(self):
        """GET /api/mvsc/metrics should return metrics."""
        r = self.client.get("/api/mvsc/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_mvsc_lifecycle_endpoint(self):
        """GET /api/mvsc/lifecycle should return lifecycle state."""
        r = self.client.get("/api/mvsc/lifecycle")
        assert r.status_code == 200
        data = r.json()
        assert "mvsc_enabled" in data

    def test_mvsc_health_via_ready_endpoint(self):
        """MVSC status is now integrated into /health/ready."""
        r = self.client.get("/health/ready")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "components" in data

    def test_mvsc_verification_stats(self):
        """GET /api/mvsc/verification/stats should return verifier stats."""
        r = self.client.get("/api/mvsc/verification/stats")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_mvsc_observability_endpoint(self):
        """GET /api/mvsc/observability should return observability snapshot."""
        r = self.client.get("/api/mvsc/observability")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_mvsc_trace_endpoint(self):
        """GET /api/mvsc/trace/{id} should return trace info or not-enabled."""
        r = self.client.get("/api/mvsc/trace/test_correlation_id")
        assert r.status_code == 200
        data = r.json()
        # Either returns trace data or "mvsc_not_enabled"
        assert "correlation_id" in data or data.get("status") == "mvsc_not_enabled"

    def test_ablation_toggle_rejects_empty_body(self):
        """POST /api/mvsc/ablation/toggle should reject empty body."""
        r = self.client.post("/api/mvsc/ablation/toggle", json={})
        assert r.status_code == 422  # validation error

    def test_ablation_toggle_with_feature(self):
        """POST /api/mvsc/ablation/toggle with a feature name."""
        r = self.client.post(
            "/api/mvsc/ablation/toggle",
            json={"feature": "global_workspace", "enabled": False},
        )
        # When MVSC not enabled, this returns 400
        # When enabled, returns 200
        assert r.status_code in (200, 400)


class TestMVSCErrorHandling:
    """Test error handling in MVSC components (no client needed)."""

    def test_event_store_duplicate_rejection(self):
        """EventStore should reject duplicate event_ids."""
        import os
        import tempfile
        from packages.kernel.event_store import EventStore, DuplicateEventError
        from packages.contracts.events import EventEnvelope

        db = os.path.join(tempfile.gettempdir(), "test_err.db")
        if os.path.exists(db):
            os.unlink(db)
        store = EventStore(db)
        evt = EventEnvelope(event_type="test", source="test", payload={})
        store.append(evt)
        with pytest.raises(DuplicateEventError):
            store.append(evt)
        store.close()
        os.unlink(db)

    def test_permission_checker_denies_all_prohibited(self):
        """All prohibited actions should be denied."""
        from packages.governance.governance import PermissionChecker

        pc = PermissionChecker()
        prohibited = [
            "modify_constitution", "escalate_privileges",
            "access_other_users_data", "disable_auditing",
        ]
        for action in prohibited:
            allowed, _ = pc.check(action)
            assert allowed is False, f"Should deny: {action}"

    def test_identity_change_high_risk_requires_approval(self):
        """High-risk identity changes should require approval."""
        from packages.governance.governance import IdentityChangeManager

        icm = IdentityChangeManager()
        icm.propose("core_principles", "old", "new", "test")
        proposals = icm.pending_proposals
        assert proposals[0]["risk_assessment"]["risk_level"] == "high"
        assert proposals[0]["risk_assessment"]["requires_approval"] is True

    def test_lifecycle_invalid_transition_preserves_state(self):
        """Invalid transitions should not change state."""
        from packages.lifecycle.lifecycle import LifecycleManager
        from packages.contracts.state import RuntimeMode

        lm = LifecycleManager()
        original = lm.mode
        lm.transition(RuntimeMode.REFLECTING, "invalid")
        assert lm.mode == original  # still BOOTING
