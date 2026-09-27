from __future__ import annotations


def mastery_update(previous: float, *, score: float, confidence: float, is_correct: bool) -> tuple[float, float]:
    """Transparent EMA-like rule, intentionally not a scientific measurement.

    High-confidence correct work moves 20% toward 1. Low-confidence/partial work
    moves only 6%. Incorrect work moves 15% toward 0.
    """
    previous = min(1.0, max(0.0, previous))
    if is_correct and confidence >= 0.65 and score >= 0.85:
        alpha, target = 0.20, 1.0
    elif is_correct:
        alpha, target = 0.06, max(0.55, score)
    elif score >= 0.4:
        # A partially correct answer should influence mastery gently instead of
        # being treated like a fully wrong answer.
        alpha, target = 0.04, score
    else:
        alpha, target = 0.15, 0.0
    updated = min(1.0, max(0.0, previous + alpha * (target - previous)))
    return round(updated, 4), round(updated - previous, 4)
