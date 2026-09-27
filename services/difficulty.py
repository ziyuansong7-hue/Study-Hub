from __future__ import annotations

from typing import Mapping

WEIGHTS = {
    "conceptual_depth": 0.25,
    "reasoning_steps": 0.20,
    "topic_integration": 0.20,
    "novelty": 0.15,
    "calculation_load": 0.10,
    "prerequisite_breadth": 0.10,
}


def difficulty_score(profile: Mapping[str, int]) -> int:
    normalized = sum(max(0, min(4, int(profile.get(key, 0)))) / 4 * weight for key, weight in WEIGHTS.items())
    return round(normalized * 100)


def difficulty_level(score: int) -> int:
    if score < 25:
        return 1
    if score < 45:
        return 2
    if score < 65:
        return 3
    if score < 80:
        return 4
    return 5


def level_target_score(level: int) -> int:
    return {1: 15, 2: 35, 3: 55, 4: 72, 5: 88}[max(1, min(5, int(level)))]


def adaptive_level(*, mastery: float, correct_streak: int = 0, failure_streak: int = 0) -> int:
    base = 1 if mastery < 0.30 else 2 if mastery < 0.50 else 3 if mastery < 0.70 else 4 if mastery < 0.88 else 5
    if correct_streak >= 2:
        base += 1
    if failure_streak >= 2:
        base -= 1
    return max(1, min(5, base))
