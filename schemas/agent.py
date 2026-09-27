from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorType(str, Enum):
    conceptual_misconception = "conceptual_misconception"
    missing_prerequisite = "missing_prerequisite"
    procedural_error = "procedural_error"
    calculation_error = "calculation_error"
    misread_question = "misread_question"
    partial_answer = "partial_answer"
    guessing = "guessing"
    none = "none"


class NextAction(str, Enum):
    advance = "advance"
    retry = "retry"
    review_prerequisite = "review_prerequisite"
    show_hint = "show_hint"
    show_explanation = "show_explanation"
    schedule_review = "schedule_review"


class Evidence(BaseModel):
    document: str
    page: int | None = None
    section: str | None = None
    quote: str = Field(max_length=500)

    @model_validator(mode="after")
    def has_locator(self):
        if self.page is None and not self.section:
            raise ValueError("evidence must include page or section")
        return self


class Question(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    external_id: str
    concept: str
    prompt: str
    difficulty: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)


class Evaluation(BaseModel):
    is_correct: bool
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    error_type: ErrorType
    evidence: list[Evidence] = Field(default_factory=list)
    feedback: str
    mastery_delta: float = Field(ge=-1, le=1)
    next_action: NextAction
    conclusion_correct: bool | None = None
    key_steps_score: float = Field(default=0.0, ge=0, le=1)
    explanation_score: float = Field(default=0.0, ge=0, le=1)
    fatal_error: bool = False

    @model_validator(mode="after")
    def correct_has_no_error(self):
        if self.is_correct and self.error_type not in {ErrorType.none, ErrorType.guessing}:
            raise ValueError("correct responses may only use none or guessing")
        return self


class AgentDecision(BaseModel):
    primary_gap: str
    rationale_summary: str
    evaluation: Evaluation
    next_question_id: str | None = None
    review_concept: str | None = None
