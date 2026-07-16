from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PredictionRecord:
    focus: str
    expected: str
    actual: str
    error: float
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PredictionTracker:
    """Tracks predictions vs actual outcomes to compute prediction error.

    Implements the theory's E_t = |P_t - S_{t+1}| core mechanic.
    Recent errors are weighted higher via exponential decay.
    """

    def __init__(self, max_history: int = 50, decay_lambda: float = 0.1) -> None:
        self.max_history = max_history
        self.decay_lambda = decay_lambda
        self.history: list[PredictionRecord] = []

    def record(self, focus: str, expected: str, actual: str) -> PredictionRecord:
        error = self._compute_error(expected, actual)
        rec = PredictionRecord(focus=focus, expected=expected, actual=actual, error=error)
        self.history.append(rec)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        return rec

    def weighted_error(self) -> float:
        """Exponentially weighted prediction error (recent errors weight more)."""
        if not self.history:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        n = len(self.history)
        for i, rec in enumerate(self.history):
            recency_index = n - i - 1
            weight = self._decay_weight(recency_index)
            weighted_sum += rec.error * weight
            total_weight += weight
        return round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0

    def recent_error(self, window: int = 5) -> float:
        if not self.history:
            return 0.0
        recent = self.history[-window:]
        return round(sum(r.error for r in recent) / len(recent), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weighted_error": self.weighted_error(),
            "recent_error": self.recent_error(),
            "history_size": len(self.history),
            "last_focus": self.history[-1].focus if self.history else "",
            "last_error": self.history[-1].error if self.history else 0.0,
        }

    def _decay_weight(self, age: int) -> float:
        import math

        return math.exp(-self.decay_lambda * age)

    @staticmethod
    def _compute_error(expected: str, actual: str) -> float:
        """Simple string-similarity-based prediction error.

        Returns 0.0 (perfect match) to 1.0 (completely different).
        """
        exp = expected.strip().lower()
        act = actual.strip().lower()
        if not exp and not act:
            return 0.0
        if not exp or not act:
            return 1.0
        if exp == act:
            return 0.0

        exp_words = set(exp.split())
        act_words = set(act.split())
        if not exp_words:
            return 1.0

        overlap = len(exp_words & act_words)
        jaccard = overlap / len(exp_words | act_words) if (exp_words | act_words) else 1.0
        return round(1.0 - jaccard, 4)
