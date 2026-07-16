from dataclasses import dataclass
from math import exp


@dataclass
class ImportanceFeatures:
    user_explicit: bool = False
    goal_related: bool = False
    blocker_related: bool = False
    persona_related: bool = False
    repeated_mentions: int = 0
    source_reliability: float = 0.5
    emotional_intensity: float = 0.0
    age_hours: float = 0.0
    # ── experience intensity (consciousness-model aligned) ──
    self_model_delta: float = 0.0
    prediction_error: float = 0.0


class ImportanceScorer:
    def score(self, f: ImportanceFeatures) -> float:
        score = 0.0

        if f.user_explicit:
            score += 0.25
        if f.goal_related:
            score += 0.20
        if f.blocker_related:
            score += 0.20
        if f.persona_related:
            score += 0.15

        score += min(f.repeated_mentions, 5) * 0.04
        score += 0.10 * f.source_reliability
        score += 0.10 * f.emotional_intensity

        # experience intensity: self-model perturbation + prediction error
        score += 0.15 * max(0.0, min(1.0, f.self_model_delta))
        score += 0.10 * max(0.0, min(1.0, f.prediction_error))

        decay = exp(-f.age_hours / 24 / 14)
        score *= decay + 0.25

        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score
