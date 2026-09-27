from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from models import Concept, Course, Document, DocumentChunk, Question
from services.agent_service import AgentService
from services.content_service import ConceptDiscoveryService
from services.data_management import create_backup
from services.database import session_scope
from services.difficulty import adaptive_level, difficulty_level, difficulty_score
from services.llm import BaseLLMProvider, FakeProvider
from services.retriever import KeywordRetriever
from services.study_plan_service import StudyPlanService
from services.study_session_service import StudySessionService
from services.todo_service import TodoService


class ExplodingProvider(BaseLLMProvider):
    def generate_json(self, *, system: str, user: str, schema: dict):
        raise AssertionError("objective questions must not call the LLM")


class CountingFakeProvider(FakeProvider):
    def __init__(self):
        self.calls = 0

    def generate_json(self, *, system: str, user: str, schema: dict):
        self.calls += 1
        return super().generate_json(system=system, user=user, schema=schema)


def test_retriever_does_not_pad_precise_evidence_with_weak_matches(db_url):
    with session_scope() as session:
        results = KeywordRetriever(session).search(1, "function domain", limit=2)
        assert len(results) == 1
        assert results[0].evidence.section == "Relations and Functions"


def test_large_document_concept_extraction_is_bounded(db_url):
    provider = CountingFakeProvider()
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        document = Document(
            course_id=course.id,
            filename="large-book.pdf",
            content_hash="large-book-test",
            status="processed",
        )
        session.add(document)
        session.flush()
        for index in range(240):
            session.add(DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                page=index + 1,
                section=f"Topic {index + 1}",
                content=f"Representative textbook content for topic {index + 1}.",
            ))
        session.flush()
        created = ConceptDiscoveryService(session, provider).extract(document.id)
        assert provider.calls == 4
        assert 1 <= created <= 40


def test_todo_center_groups_future_overdue_inbox_and_completed(db_url):
    now = datetime.now().replace(microsecond=0)
    with session_scope() as session:
        service = TodoService(session)
        inbox = service.create(title="Inbox note", due_at=None, priority="low", category="personal")
        overdue = service.create(
            title="Late interview", due_at=now - timedelta(hours=2),
            priority="high", category="interview",
        )
        upcoming = service.create(
            title="Future project", due_at=now + timedelta(days=5),
            priority="normal", category="project",
        )
        groups = service.grouped()
        assert inbox in groups["inbox"]
        assert overdue in groups["overdue"]
        assert upcoming in groups["upcoming"]

        service.edit(
            upcoming.id, title="Edited project", due_at=now + timedelta(days=7),
            notes="milestone", priority="high", category="project",
        )
        service.action(overdue.id, "complete")
        groups = service.grouped()
        assert overdue in groups["completed"]
        assert upcoming.title == "Edited project" and upcoming.priority == "high"


def test_difficulty_model_is_explainable_and_adaptive():
    profile = {
        "conceptual_depth": 3,
        "reasoning_steps": 3,
        "topic_integration": 3,
        "novelty": 3,
        "calculation_load": 2,
        "prerequisite_breadth": 3,
    }
    score = difficulty_score(profile)
    assert score == 73
    assert difficulty_level(score) == 4
    assert adaptive_level(mastery=0.8, correct_streak=2) == 5
    assert adaptive_level(mastery=0.8, failure_streak=2) == 3


def test_objective_question_is_graded_without_llm(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        concept = session.scalar(select(Concept).where(Concept.slug == "logic-propositions"))
        question = Question(
            course_id=course.id,
            concept_id=concept.id,
            external_id="objective-direct-grade",
            prompt="A conjunction is true only when both operands are true.",
            expected_answer="true",
            question_type="true_false",
            options=["true", "false"],
            source_type="manual",
            moderation_status="approved",
        )
        session.add(question)
        session.flush()
        decision = AgentService(session, ExplodingProvider()).submit_attempt(
            attempt_id="objective-attempt",
            question_id=question.external_id,
            answer="TRUE",
            confidence=0.9,
        )
        assert decision.evaluation.is_correct is True
        assert decision.evaluation.conclusion_correct is True
        assert decision.evaluation.key_steps_score == 1


def test_minute_task_timer_completes_target(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        service = StudyPlanService(session)
        task = service.create_manual(
            course_id=course.id,
            concept_id=None,
            title="Timed review",
            mode="practice",
            target_type="minutes",
            target_value=1,
            due_date=date.today(),
        )
        service.timer_action(task.id, "start")
        task.timer_started_at = datetime.now(timezone.utc) - timedelta(seconds=70)
        service.timer_action(task.id, "pause")
        assert task.accumulated_seconds >= 70
        assert task.progress_value == 1
        assert task.status == "completed"


def test_session_uses_only_approved_questions(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        concept = session.scalar(select(Concept).where(Concept.slug == "functions"))
        approved = session.scalar(select(Question).where(Question.external_id == "demo-q7"))
        approved.moderation_status = "rejected"
        service = StudySessionService(session)
        with pytest.raises(ValueError, match="approved questions"):
            service.create(
                course_id=course.id, mode="focus", concept_id=concept.id,
                difficulty=0.5, difficulty_level=3, target_count=1,
                question_type="any",
            )
        approved.moderation_status = "approved"
        study_session = service.create(
            course_id=course.id, mode="focus", concept_id=concept.id,
            difficulty=0.5, difficulty_level=3, target_count=1,
            question_type="any",
        )
        assert study_session.items[0].question_id == approved.id


def test_sqlite_backup_is_consistent(db_url):
    backup = create_backup(db_url)
    try:
        connection = sqlite3.connect(backup)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            connection.close()
        assert {"courses", "questions", "attempts", "todo_items"} <= tables
    finally:
        backup.unlink(missing_ok=True)
