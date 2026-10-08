"""Public runtime wiring, separate from requested experimental feature flags."""

from typing import Any


def runtime_observation(container: Any) -> dict[str, Any]:
    controller = container.runtime.controller
    config = getattr(container, "settings", None)
    snapshot: dict[str, Any] = (
        controller.snapshot()
        if controller
        else {
            "mode": "unknown",
            "phase": "unavailable",
            "accepting_events": False,
        }
    )
    snapshot["requested"] = {
        "enable_minimal_brain": getattr(config, "enable_minimal_brain", None),
        "enable_mvsc_pipeline": getattr(config, "enable_mvsc_pipeline", None),
        "enable_business_goals": getattr(config, "enable_business_goals", None),
        "enable_processing_episodes": getattr(
            config, "enable_processing_episodes", None
        ),
        "enable_durable_requests": getattr(config, "enable_durable_requests", None),
        "resume_durable_requests": getattr(config, "resume_durable_requests", None),
        "enable_action_context": getattr(config, "enable_action_context", None),
    }
    snapshot["extensions"] = {
        "mvsc": dict(
            container.system_state.get(
                "mvsc_status",
                {
                    "requested": getattr(config, "enable_mvsc_pipeline", None),
                    "status": "unknown",
                    "attached": False,
                    "is_http_consumer": False,
                },
            )
        )
    }
    attached = (
        getattr(getattr(container, "integrations", None), "business_goals", None)
        is not None
    )
    snapshot["extensions"]["business_goals"] = {
        "requested": getattr(config, "enable_business_goals", None),
        "attached": attached,
        "receipt_observer_errors": container.runtime.results.stats().get(
            "receipt_observer_errors", 0
        )
        if attached
        else 0,
        "business_verifier_attached": attached
        and container.integrations.business_goals.verifier is not None,
    }
    journal = getattr(
        getattr(container, "integrations", None), "processing_episodes", None
    )
    snapshot["extensions"]["processing_episodes"] = {
        "requested": getattr(config, "enable_processing_episodes", None),
        "attached": journal is not None,
    }
    if journal is not None:
        try:
            snapshot["extensions"]["processing_episodes"].update(journal.stats())
        except Exception:
            snapshot["extensions"]["processing_episodes"]["stats_unavailable"] = True
    requests = getattr(
        getattr(container, "integrations", None), "durable_requests", None
    )
    snapshot["extensions"]["durable_requests"] = {
        "requested": getattr(config, "enable_durable_requests", None),
        "attached": requests is not None,
    }
    if requests is not None:
        try:
            snapshot["extensions"]["durable_requests"].update(requests.stats())
        except Exception:
            snapshot["extensions"]["durable_requests"]["stats_unavailable"] = True
    publisher = getattr(container.runtime, "request_recovery", None)
    snapshot["extensions"]["request_recovery"] = {
        "attached": publisher is not None,
        **(publisher.stats if publisher is not None else {}),
    }
    return snapshot
