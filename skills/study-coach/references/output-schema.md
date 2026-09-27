# Structured decision output

Return an `AgentDecision` object:

```json
{
  "primary_gap": "one concept name",
  "rationale_summary": "concise, no private reasoning",
  "evaluation": {
    "is_correct": false,
    "score": 0.0,
    "confidence": 0.6,
    "error_type": "conceptual_misconception",
    "evidence": [{"document": "name", "page": 1, "section": null, "quote": "short excerpt"}],
    "feedback": "correct conclusion, concise solution explanation, and one primary error when applicable",
    "mastery_delta": -0.04,
    "next_action": "show_explanation"
  },
  "next_question_id": null,
  "review_concept": null
}
```

Allowed `next_action` values only: `advance`, `retry`, `review_prerequisite`, `show_hint`, `show_explanation`, `schedule_review`.
