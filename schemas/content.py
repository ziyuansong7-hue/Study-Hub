from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ConceptSuggestion(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    source_chunk_ids: list[int] = Field(default_factory=list)
    recommended_action: Literal["create", "merge", "rename", "ignore", "review"] = "create"
    importance: Literal["core", "supporting", "optional"] = "supporting"
    confidence: float = Field(default=0.6, ge=0, le=1)
    chapter: str = Field(default="", max_length=255)
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(default="", max_length=500)
    merge_target: str | None = Field(default=None, max_length=255)


class ConceptExtraction(BaseModel):
    concepts: list[ConceptSuggestion] = Field(min_length=1, max_length=30)


class ConceptReviewItem(BaseModel):
    candidate_id: int
    recommended_action: Literal["create", "merge", "rename", "ignore", "review"]
    importance: Literal["core", "supporting", "optional"]
    confidence: float = Field(ge=0, le=1)
    chapter: str = Field(default="", max_length=255)
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=2, max_length=500)
    merge_target: str | None = Field(default=None, max_length=255)


class ConceptReviewBatch(BaseModel):
    reviews: list[ConceptReviewItem] = Field(min_length=1, max_length=30)


class DifficultyProfile(BaseModel):
    conceptual_depth: int = Field(ge=0, le=4)
    reasoning_steps: int = Field(ge=0, le=4)
    topic_integration: int = Field(ge=0, le=4)
    novelty: int = Field(ge=0, le=4)
    calculation_load: int = Field(ge=0, le=4)
    prerequisite_breadth: int = Field(ge=0, le=4)


class GeneratedQuestion(BaseModel):
    learning_objective: str = Field(default="", max_length=1000)
    prompt: str = Field(min_length=5, max_length=2000)
    expected_answer: str = Field(min_length=1, max_length=4000)
    solution_steps: list[str] = Field(default_factory=list, max_length=12)
    rubric_points: list[str] = Field(default_factory=list, max_length=12)
    question_type: Literal["single_choice", "multiple_choice", "short_answer", "true_false", "calculation"]
    options: list[str] = Field(default_factory=list, max_length=8)
    evidence_query: str = Field(default="", max_length=255)
    source_chunk_ids: list[int] = Field(default_factory=list)
    difficulty_profile: DifficultyProfile
    expected_minutes: int = Field(ge=1, le=90)

    @model_validator(mode="after")
    def choices_have_options(self):
        if self.question_type in {"single_choice", "multiple_choice"} and len(self.options) < 2:
            raise ValueError("choice questions require at least two options")
        return self


class QuestionGeneration(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=20)


class DifficultyAssessment(BaseModel):
    index: int = Field(ge=0)
    difficulty_profile: DifficultyProfile
    difficulty_score: int = Field(ge=0, le=100)
    difficulty_level: int = Field(ge=1, le=5)
    expected_minutes: int = Field(ge=1, le=90)
    rationale: str = Field(min_length=3, max_length=1000)
    ambiguity_detected: bool = False
    within_course_scope: bool = True


class DifficultyReviewBatch(BaseModel):
    assessments: list[DifficultyAssessment] = Field(min_length=1, max_length=20)
