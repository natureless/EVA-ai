"""Single boundary between the stable runtime and experimental extensions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("eva.experimental")


def start_mvsc(container: Any, settings: Any) -> dict[str, Any] | None:
    """Enable MVSC without leaking experimental imports into the core runtime."""
    if not settings.enable_mvsc_pipeline:
        return None
    try:
        from packages.kernel.mvsc_bootstrap import integrate_mvsc

        components = integrate_mvsc(container, settings)
        logger.info("MVSC pipeline enabled")
        return components
    except Exception:
        logger.exception("MVSC integration failed; stable cognition loop remains active")
        return None


def stop_mvsc(components: dict[str, Any] | None) -> None:
    if not components:
        return
    try:
        from packages.kernel.mvsc_bootstrap import shutdown_mvsc

        shutdown_mvsc(components)
    except Exception:
        logger.exception("MVSC shutdown failed")


def conscious_state(system_state: dict[str, Any], *, subject_id: str = "eva-001") -> Any:
    from packages.kernel.state_bridge import system_state_to_conscious

    return system_state_to_conscious(system_state, subject_id=subject_id)


def toggle_mvsc_feature(container: Any, feature: str, enabled: bool) -> bool:
    from packages.mvsc_lab.integration import toggle_feature

    return toggle_feature(container, feature, enabled)
