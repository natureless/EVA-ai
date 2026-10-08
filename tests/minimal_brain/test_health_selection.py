from types import SimpleNamespace
from unittest.mock import Mock

from runtime.health import HealthService


def test_health_observes_selected_cognitive_service():
    health = HealthService({}, loop=SimpleNamespace(is_running=False))
    selected = SimpleNamespace(is_running=True)
    health.set_cognition_loop(selected)
    assert health.ready()["components"]["cognition_loop"] is True
    selected.is_running = False
    assert health.ready()["components"]["cognition_loop"] is False


def test_health_observes_all_stable_cognition_threads():
    first, second = Mock(), Mock()
    first.is_alive.return_value = True
    second.is_alive.return_value = True
    health = HealthService({}, loop=SimpleNamespace(_threads=[first, second]))
    assert health._probe_cognition_loop() is True
    second.is_alive.return_value = False
    assert health._probe_cognition_loop() is False
