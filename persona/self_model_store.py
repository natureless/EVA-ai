import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from runtime.file_utils import atomic_json_save


MAX_STATE_HISTORY = 50
MAX_PERTURBATIONS = 20
MAX_PREDICTION_ERRORS = 100

DEFAULT_SELF_MODEL: dict[str, Any] = {
    "identity": "EVA v0.1",
    "status": "active",
    "capabilities": [
        "chat",
        "docs_summary",
        "memory_write",
        "trace_record",
    ],
    "constraints": [
        "single_process",
        "single_round_single_agent",
    ],
    "state_history": [],
    "perturbations": [],
    "prediction_errors": [],
    "stability_metrics": {
        "total_perturbations": 0,
        "mean_prediction_error": 0.0,
        "recent_perturbation_count": 0,
        "stability_score": 1.0,
        "last_evaluated_at": None,
    },
}

CURRENT_SELF_MODEL_VERSION = 2


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate old self-model (without dynamics fields) to v2."""
    payload.setdefault("state_history", [])
    payload.setdefault("perturbations", [])
    payload.setdefault("prediction_errors", [])
    payload.setdefault(
        "stability_metrics",
        {
            "total_perturbations": 0,
            "mean_prediction_error": 0.0,
            "recent_perturbation_count": 0,
            "stability_score": 1.0,
            "last_evaluated_at": None,
        },
    )
    payload["_version"] = 2
    return payload


class SelfModelStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_or_init(self) -> dict[str, Any]:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            version = payload.get("_version", 1)
            if version < CURRENT_SELF_MODEL_VERSION:
                payload = _migrate_v1_to_v2(payload)
            return payload
        default = DEFAULT_SELF_MODEL.copy()
        default["_version"] = CURRENT_SELF_MODEL_VERSION
        self.save(default)
        return default

    def save(self, payload: dict[str, Any]) -> None:
        atomic_json_save(self.path, payload)

    # ── dynamics helpers ──────────────────────────────────────

    def record_state_change(
        self,
        payload: dict[str, Any],
        *,
        change_type: str,
        detail: str,
        focus: str = "",
        loop_id: str = "",
    ) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": change_type,
            "detail": detail,
            "focus": focus,
            "loop_id": loop_id,
        }
        history = payload.setdefault("state_history", [])
        history.append(entry)
        if len(history) > MAX_STATE_HISTORY:
            payload["state_history"] = history[-MAX_STATE_HISTORY:]
        return payload

    def record_prediction_error(
        self,
        payload: dict[str, Any],
        *,
        error: float,
        focus: str = "",
    ) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "error": round(error, 4),
            "focus": focus,
        }
        errors = payload.setdefault("prediction_errors", [])
        errors.append(entry)
        if len(errors) > MAX_PREDICTION_ERRORS:
            payload["prediction_errors"] = errors[-MAX_PREDICTION_ERRORS:]

        recent = errors[-20:]
        mean_err = sum(e["error"] for e in recent) / len(recent)
        payload.setdefault("stability_metrics", {})
        payload["stability_metrics"]["mean_prediction_error"] = round(mean_err, 4)
        payload["stability_metrics"]["last_evaluated_at"] = entry["ts"]
        return payload

    def record_perturbation(
        self,
        payload: dict[str, Any],
        *,
        cause: str,
        delta_magnitude: float,
        affected_fields: list[str],
        loop_id: str = "",
    ) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cause": cause,
            "delta_magnitude": round(delta_magnitude, 4),
            "affected_fields": affected_fields,
            "loop_id": loop_id,
        }
        perturbations = payload.setdefault("perturbations", [])
        perturbations.append(entry)
        if len(perturbations) > MAX_PERTURBATIONS:
            payload["perturbations"] = perturbations[-MAX_PERTURBATIONS:]

        metrics = payload.setdefault("stability_metrics", {})
        metrics["total_perturbations"] = metrics.get("total_perturbations", 0) + 1
        metrics["recent_perturbation_count"] = len(perturbations)

        if delta_magnitude > 0:
            metrics["stability_score"] = round(
                max(0.0, 1.0 - delta_magnitude * 0.5), 4
            )
        return payload
