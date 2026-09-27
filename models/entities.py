from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Course(TimestampMixin, Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    code_is_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    material_language: Mapped[str] = mapped_column(String(16), default="en")
    explanation_language: Mapped[str] = mapped_column(String(16), default="en")
    terminology_policy: Mapped[str] = mapped_column(String(32), default="preserve_source")
    color: Mapped[str] = mapped_column(String(16), default="#ff9000")
    icon: Mapped[str] = mapped_column(String(32), default="book")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_studied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_ai_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def display_code(self) -> str:
        return "" if self.code_is_generated else self.code


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("course_id", "content_hash", name="uq_document_hash"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="processed")
    error_message: Mapped[str | None] = mapped_column(Text)
    source_category: Mapped[str] = mapped_column(String(32), default="course_material", index=True)
    source_year: Mapped[int | None] = mapped_column(Integer)
    original_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    material_type: Mapped[str] = mapped_column(String(32), default="auto", index=True)
    material_type_source: Mapped[str] = mapped_column(String(16), default="auto")
    detected_material_type: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    analysis_mode: Mapped[str] = mapped_column(String(32), default="quick")
    analysis_status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    analysis_provider: Mapped[str] = mapped_column(String(64), default="local")
    analysis_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis_version: Mapped[int] = mapped_column(Integer, default=1)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    course: Mapped[Course] = relationship()


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
        Index("ix_chunk_search", "document_id", "section"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(255))
    chunk_type: Mapped[str] = mapped_column(String(32), default="content", index=True)
    quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    usable_for_generation: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    document: Mapped[Document] = relationship()


class DocumentSection(TimestampMixin, Base):
    __tablename__ = "document_sections"
    __table_args__ = (
        UniqueConstraint("document_id", "section_key", name="uq_document_section_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    section_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(32), default="section")
    position: Mapped[int] = mapped_column(Integer, default=0)
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="parser")
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    document: Mapped[Document] = relationship()


class Concept(TimestampMixin, Base):
    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("course_id", "slug", name="uq_concept_slug"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    chapter: Mapped[str] = mapped_column(String(255), default="", index=True)
    chapter_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    granularity: Mapped[str] = mapped_column(String(32), default="concept", index=True)
    importance: Mapped[str] = mapped_column(String(32), default="supporting", index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    course: Mapped[Course] = relationship()


class ConceptCandidate(TimestampMixin, Base):
    __tablename__ = "concept_candidates"
    __table_args__ = (UniqueConstraint("document_id", "name", name="uq_candidate_document_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    recommended_action: Mapped[str] = mapped_column(String(32), default="create", index=True)
    importance: Mapped[str] = mapped_column(String(32), default="supporting", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    chapter: Mapped[str] = mapped_column(String(255), default="", index=True)
    prerequisite_names: Mapped[list] = mapped_column(JSON, default=list)
    recommendation_reason: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified", index=True)
    suggested_merge_concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    confirmed_concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), index=True
    )
    document: Mapped[Document] = relationship()
    confirmed_concept: Mapped[Concept | None] = relationship(foreign_keys=[confirmed_concept_id])
    suggested_merge_concept: Mapped[Concept | None] = relationship(foreign_keys=[suggested_merge_concept_id])


class ConceptSource(TimestampMixin, Base):
    __tablename__ = "concept_sources"
    __table_args__ = (UniqueConstraint("concept_id", "chunk_id", name="uq_concept_source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True)
    concept: Mapped[Concept] = relationship()
    chunk: Mapped[DocumentChunk] = relationship()


class ConceptEdge(TimestampMixin, Base):
    __tablename__ = "concept_edges"
    __table_args__ = (
        UniqueConstraint("prerequisite_id", "dependent_id", name="uq_concept_edge"),
        CheckConstraint("prerequisite_id != dependent_id", name="ck_no_self_edge"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prerequisite_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    dependent_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    prerequisite: Mapped[Concept] = relationship(foreign_keys=[prerequisite_id])
    dependent: Mapped[Concept] = relationship(foreign_keys=[dependent_id])


class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("course_id", "external_id", name="uq_question_external"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str] = mapped_column(Text, nullable=False)
    rubric: Mapped[dict] = mapped_column(JSON, default=dict)
    learning_objective: Mapped[str] = mapped_column(Text, default="")
    solution_steps: Mapped[list] = mapped_column(JSON, default=list)
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    difficulty_level: Mapped[int] = mapped_column(Integer, default=3, index=True)
    difficulty_score: Mapped[int] = mapped_column(Integer, default=50)
    difficulty_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_minutes: Mapped[int] = mapped_column(Integer, default=5)
    difficulty_rationale: Mapped[str] = mapped_column(Text, default="")
    evidence_query: Mapped[str] = mapped_column(String(255), default="")
    source_type: Mapped[str] = mapped_column(String(32), default="ai_generated", index=True)
    source_policy: Mapped[str] = mapped_column(String(32), default="reference_guided", index=True)
    quality_status: Mapped[str] = mapped_column(String(32), default="passed", index=True)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    generation_provider: Mapped[str] = mapped_column(String(64), default="unknown")
    question_type: Mapped[str] = mapped_column(String(32), default="short_answer")
    options: Mapped[list] = mapped_column(JSON, default=list)
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    moderation_status: Mapped[str] = mapped_column(String(32), default="approved", index=True)
    review_notes: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    concept: Mapped[Concept] = relationship()
    course: Mapped[Course] = relationship()


class Attempt(TimestampMixin, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_attempt_confidence"),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_attempt_score"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    feedback: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    grading_details: Mapped[dict] = mapped_column(JSON, default=dict)
    question: Mapped[Question] = relationship()


class MasteryRecord(TimestampMixin, Base):
    __tablename__ = "mastery_records"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_mastery_attempt"),
        CheckConstraint("mastery >= 0 AND mastery <= 1", name="ck_mastery_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.attempt_id", ondelete="SET NULL"))
    mastery: Mapped[float] = mapped_column(Float, nullable=False)
    delta: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(255), default="")
    concept: Mapped[Concept] = relationship()


class StudentConceptState(TimestampMixin, Base):
    __tablename__ = "student_concept_states"
    __table_args__ = (
        UniqueConstraint("student_id", "concept_id", name="uq_student_concept_state"),
        CheckConstraint("mastery >= 0 AND mastery <= 1", name="ck_state_mastery_range"),
        CheckConstraint("average_confidence >= 0 AND average_confidence <= 1", name="ck_state_confidence_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    mastery: Mapped[float] = mapped_column(Float, default=0.25)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correct_streak: Mapped[int] = mapped_column(Integer, default=0)
    failure_streak: Mapped[int] = mapped_column(Integer, default=0)
    average_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_type: Mapped[str] = mapped_column(String(64), default="none")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    concept: Mapped[Concept] = relationship()


class ReviewTask(TimestampMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint("student_id", "concept_id", "due_date", name="uq_review_day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    concept: Mapped[Concept] = relationship()


class StudyTask(TimestampMixin, Base):
    __tablename__ = "study_tasks"
    __table_args__ = (
        UniqueConstraint("recommendation_key", name="uq_study_task_recommendation"),
        CheckConstraint("target_value > 0", name="ck_study_task_target_value"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="focus")
    target_type: Mapped[str] = mapped_column(String(32), default="questions")
    target_value: Mapped[int] = mapped_column(Integer, default=5)
    progress_value: Mapped[int] = mapped_column(Integer, default=0)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    recommendation_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accumulated_seconds: Mapped[int] = mapped_column(Integer, default=0)
    timer_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    course: Mapped[Course] = relationship()
    concept: Mapped[Concept | None] = relationship()


class TodoItem(TimestampMixin, Base):
    __tablename__ = "todo_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    due_kind: Mapped[str] = mapped_column(String(16), default="date")
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    category: Mapped[str] = mapped_column(String(32), default="personal", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeScopePreset(TimestampMixin, Base):
    __tablename__ = "practice_scope_presets"
    __table_args__ = (
        UniqueConstraint("student_id", "course_id", "name", name="uq_practice_scope_name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    difficulty_level: Mapped[int] = mapped_column(Integer, default=0)
    target_count: Mapped[int] = mapped_column(Integer, default=10)
    question_type: Mapped[str] = mapped_column(String(32), default="any")
    material_only: Mapped[bool] = mapped_column(Boolean, default=False)
    course: Mapped[Course] = relationship()


class StudySession(TimestampMixin, Base):
    __tablename__ = "study_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="SET NULL"), index=True)
    concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    study_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="SET NULL"), index=True
    )
    parent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="SET NULL"), index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), default="any")
    source_policy: Mapped[str] = mapped_column(String(32), default="reference_guided")
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    difficulty_level: Mapped[int] = mapped_column(Integer, default=3)
    target_count: Mapped[int] = mapped_column(Integer, default=5)
    material_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    course: Mapped[Course] = relationship()
    concept: Mapped[Concept | None] = relationship()
    study_task: Mapped[StudyTask | None] = relationship()
    parent_session: Mapped[StudySession | None] = relationship(remote_side="StudySession.id")
    items: Mapped[list[SessionItem]] = relationship(
        back_populates="study_session", order_by="SessionItem.position", cascade="all, delete-orphan"
    )


class SessionItem(TimestampMixin, Base):
    __tablename__ = "session_items"
    __table_args__ = (UniqueConstraint("study_session_id", "position", name="uq_session_position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_session_id: Mapped[int] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("attempts.attempt_id", ondelete="SET NULL"), unique=True
    )
    study_session: Mapped[StudySession] = relationship(back_populates="items")
    question: Mapped[Question] = relationship()


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    student_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error: Mapped[str | None] = mapped_column(Text)
