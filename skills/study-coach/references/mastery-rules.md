# Mastery rules

StudyHub uses a bounded explanatory EMA-like estimate:

- Start unseen concepts at `0.25`.
- Correct, score at least `0.85`, confidence at least `0.65`: move 20% toward `1.0`.
- Correct but partial or low-confidence: move 6% toward `max(0.55, score)`.
- Incorrect: move 15% toward `0.0`.
- Clamp every result to `[0, 1]` and store the change as `mastery_delta`.

Describe this as a heuristic for sequencing practice. Never call it a precise cognitive measurement.
