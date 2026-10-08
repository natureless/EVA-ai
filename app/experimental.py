"""Single boundary between the stable runtime and experimental extensions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("eva.experimental")


def build_durable_requests(settings: Any) -> Any:
    """Prepare storage only; bootstrap orders cross-store receipt recovery."""
    if not settings.enable_durable_requests:
        return None
    if settings.storage_backend != "sqlite":
        raise ValueError("durable requests require SQLite")
    from packages.kernel.request_dispatch_store import RequestDispatchStore

    return RequestDispatchStore(
        settings.db_path,
        subject_id=f"{settings.mvsc_subject_id}:requests",
        capacity=settings.result_registry_capacity,
        enable_action_context=settings.enable_action_context,
    )


def build_processing_episodes(settings: Any, *, receipt_lookup=None) -> Any:
    if not settings.enable_processing_episodes:
        return None
    if settings.storage_backend != "sqlite":
        raise ValueError("processing Episodes require SQLite")
    from packages.kernel.processing_episodes import ProcessingEpisodes
    from packages.minimal_brain.file_verifier import WorkspaceFileVerifier

    store = ProcessingEpisodes(
        settings.db_path,
        subject_id=settings.mvsc_subject_id,
        file_verifier=WorkspaceFileVerifier(settings.base_dir),
    )
    try:
        store.recover(receipt_lookup=receipt_lookup)
    except BaseException:
        store.close()
        raise
    return store


def build_business_goals(settings: Any, *, recover: bool = True) -> Any:
    """Opt-in goal storage, shared by either consumer through canonical receipts."""
    if not settings.enable_business_goals:
        return None
    if settings.storage_backend != "sqlite":
        raise ValueError("business goals require the SQLite storage backend")
    from packages.minimal_brain.business_goal_store import BusinessGoalStore
    from packages.minimal_brain.file_verifier import WorkspaceFileVerifier

    store = BusinessGoalStore(
        settings.db_path, verifier=WorkspaceFileVerifier(settings.base_dir)
    )
    try:
        if recover:
            store.recover()
    except BaseException:
        store.close()
        raise
    return store


def build_minimal_brain(container: Any, settings: Any) -> Any:
    """Construct the optional consumer; RuntimeController owns its lifecycle."""
    if not settings.enable_minimal_brain:
        return None
    if settings.enable_mvsc_pipeline:
        raise ValueError("minimal brain and MVSC cannot be enabled together")
    from packages.minimal_brain.integration import create_minimal_brain

    kernel = create_minimal_brain(container, settings)
    container.integrations.minimal_brain = kernel
    return kernel


def start_minimal_brain(container: Any, settings: Any) -> Any:
    """Compatibility entry point; application composition uses build_minimal_brain."""
    kernel = build_minimal_brain(container, settings)
    if kernel is None:
        return None
    kernel.start()
    logger.info("minimal brain enabled; stable event consumer remains unstarted")
    return kernel


def start_mvsc(container: Any, settings: Any) -> dict[str, Any] | None:
    """Attach experimental adapters; this does not replace the HTTP consumer."""
    status = {
        "requested": bool(settings.enable_mvsc_pipeline),
        "status": "disabled",
        "attached": False,
        "is_http_consumer": False,
    }
    container.system_state["mvsc_status"] = status
    if not settings.enable_mvsc_pipeline:
        return None
    if getattr(settings, "enable_minimal_brain", False):
        raise ValueError("minimal brain and MVSC cannot be enabled together")
    try:
        from packages.kernel.mvsc_bootstrap import integrate_mvsc

        components = integrate_mvsc(container, settings)
        status.update(status="attached", attached=True)
        logger.info("MVSC experimental adapters attached; HTTP consumer remains legacy")
        return components
    except Exception:
        status["status"] = "failed"
        logger.exception(
            "MVSC integration failed; stable cognition loop remains active"
        )
        return None


def stop_mvsc(components: dict[str, Any] | None) -> bool:
    if not components:
        return True
    try:
        from packages.kernel.mvsc_bootstrap import shutdown_mvsc

        shutdown_mvsc(components)
        return True
    except Exception:
        logger.exception("MVSC shutdown failed")
        return False


def conscious_state(
    system_state: dict[str, Any], *, subject_id: str = "eva-001"
) -> Any:
    from packages.kernel.state_bridge import system_state_to_conscious

    return system_state_to_conscious(system_state, subject_id=subject_id)


def toggle_mvsc_feature(container: Any, feature: str, enabled: bool) -> bool:
    from packages.mvsc_lab.integration import toggle_feature

    return toggle_feature(container, feature, enabled)
