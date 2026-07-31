"""Unit tests for HealthService component probes."""

from unittest.mock import MagicMock

import pytest

from runtime.health import HealthService


class TestHealthService:
    def test_live_always_alive(self):
        svc = HealthService({})
        assert svc.live() == {"status": "alive"}

    def test_ready_when_state_ready(self):
        svc = HealthService({"ready": True})
        result = svc.ready()
        assert result["status"] == "ready"

    def test_ready_when_not_ready(self):
        svc = HealthService({"ready": False})
        result = svc.ready()
        assert result["status"] == "not_ready"

    def test_ready_includes_all_component_keys(self):
        svc = HealthService({})
        result = svc.ready()
        components = result["components"]
        expected_keys = {
            "db", "event_bus", "event_persistence", "planner", "registry",
            "result_registry", "snapshot", "profile", "persona",
            "self_model", "scheduler", "proactive", "cognition_loop", "llm",
        }
        assert set(components.keys()) == expected_keys

    def test_db_probe_with_store(self):
        store = MagicMock()
        store.fetchall.return_value = [(1,)]
        svc = HealthService({}, store=store)
        assert svc._probe_db() is True

    def test_db_probe_store_returns_none(self):
        store = MagicMock()
        store.fetchall.return_value = None
        svc = HealthService({}, store=store)
        assert svc._probe_db() is False

    def test_db_probe_store_raises(self):
        store = MagicMock()
        store.fetchall.side_effect = RuntimeError("db down")
        svc = HealthService({}, store=store)
        assert svc._probe_db() is False

    def test_db_probe_fallback_to_state(self):
        svc = HealthService({"db_ready": True})
        assert svc._probe_db() is True

    def test_event_bus_probe_healthy(self):
        bus = MagicMock()
        bus.size.return_value = 5
        bus._persist_healthy = True
        svc = HealthService({}, event_bus=bus)
        assert svc._probe_event_bus() is True

    def test_event_bus_probe_persist_unhealthy(self):
        bus = MagicMock()
        bus.size.return_value = 5
        bus._persist_healthy = False
        svc = HealthService({}, event_bus=bus)
        assert svc._probe_event_bus() is False

    def test_event_bus_probe_fallback(self):
        svc = HealthService({"event_bus_ready": True})
        assert svc._probe_event_bus() is True

    def test_event_persistence_probe(self):
        bus = MagicMock()
        bus._persist_healthy = True
        svc = HealthService({}, event_bus=bus)
        assert svc._probe_event_persistence() is True

    def test_event_persistence_default_true(self):
        svc = HealthService({})
        assert svc._probe_event_persistence() is True

    def test_event_depth_with_bus(self):
        bus = MagicMock()
        bus.size.return_value = 42
        svc = HealthService({}, event_bus=bus)
        assert svc._probe_event_depth() == 42

    def test_event_depth_fallback(self):
        svc = HealthService({"pending_events": 10})
        assert svc._probe_event_depth() == 10

    def test_cognition_loop_probe_alive(self):
        loop = MagicMock()
        loop._thread = MagicMock()
        loop._thread.is_alive.return_value = True
        svc = HealthService({}, loop=loop)
        assert svc._probe_cognition_loop() is True

    def test_cognition_loop_probe_dead(self):
        loop = MagicMock()
        loop._thread = MagicMock()
        loop._thread.is_alive.return_value = False
        svc = HealthService({}, loop=loop)
        assert svc._probe_cognition_loop() is False

    def test_cognition_loop_no_thread(self):
        loop = MagicMock()
        loop._thread = None
        svc = HealthService({}, loop=loop)
        assert svc._probe_cognition_loop() is False

    def test_cognition_loop_fallback(self):
        svc = HealthService({"loop_ready": True})
        assert svc._probe_cognition_loop() is True

    def test_scheduler_probe_running(self):
        sched = MagicMock()
        sched._scheduler = MagicMock()
        sched._scheduler.running = True
        svc = HealthService({}, scheduler=sched)
        assert svc._probe_scheduler() is True

    def test_scheduler_probe_not_running(self):
        sched = MagicMock()
        sched._scheduler = MagicMock()
        sched._scheduler.running = False
        svc = HealthService({}, scheduler=sched)
        assert svc._probe_scheduler() is False

    def test_scheduler_probe_fallback(self):
        svc = HealthService({"scheduler_ready": True})
        assert svc._probe_scheduler() is True
