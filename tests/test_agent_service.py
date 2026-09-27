from __future__ import annotations

from sqlalchemy import func, select

from models import Attempt, Concept, Course, MasteryRecord, Question, StudentConceptState
from schemas import ErrorType, NextAction
from scripts.repair_answer_grading import repair
from services.agent_service import AgentService
from services.answer_grading import grade_short_answer
from services.database import session_scope
from services.llm import FakeProvider
from services.retriever import BaseRetriever


class EmptyRetriever(BaseRetriever):
    def search(self, course_id: int, query: str, limit: int = 5):
        return []


class MissingPrerequisiteProvider(FakeProvider):
    def generate_json(self, **kwargs):
        result = super().generate_json(**kwargs)
        result.update(error_type="missing_prerequisite", is_correct=False, score=0, next_action="review_prerequisite")
        return result


def test_consecutive_correct_answers_raise_mastery(db_url):
    with session_scope() as session:
        service = AgentService(session, FakeProvider())
        service.submit_attempt(attempt_id="a1", question_id="demo-q7", answer="exactly one", confidence=0.9)
        service.submit_attempt(attempt_id="a2", question_id="demo-q7", answer="exactly one", confidence=0.9)
        values = list(session.scalars(select(MasteryRecord.mastery).order_by(MasteryRecord.id)))
        assert values[-1] > values[-2] > 0.25


def test_low_confidence_correct_only_small_increase(db_url):
    with session_scope() as session:
        decision = AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="guess", question_id="demo-q7", answer="exactly one", confidence=0.2
        )
        assert decision.evaluation.error_type == ErrorType.guessing
        assert 0 < decision.evaluation.mastery_delta < 0.05
        assert decision.evaluation.next_action == NextAction.retry


def test_short_answer_accepts_equivalent_induction_terminology(db_url):
    with session_scope() as session:
        decision = AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="induction-alias",
            question_id="demo-q8",
            answer="base case and inductive case",
            confidence=0.9,
        )
        attempt = session.scalar(select(Attempt).where(Attempt.attempt_id == "induction-alias"))
        assert decision.evaluation.is_correct is True
        assert decision.evaluation.score == 1.0
        assert decision.evaluation.fatal_error is False
        assert attempt.grading_details["grader"] == "local_key_concepts"


def test_short_answer_awards_partial_credit_without_fatal_error(db_url):
    with session_scope() as session:
        decision = AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="induction-partial",
            question_id="demo-q8",
            answer="base case",
            confidence=0.8,
        )
        assert decision.evaluation.is_correct is False
        assert decision.evaluation.score == 0.5
        assert decision.evaluation.error_type == ErrorType.partial_answer
        assert decision.evaluation.fatal_error is False
        assert decision.evaluation.mastery_delta >= 0


def test_unmatched_structured_rubric_defers_to_semantic_grader(db_url):
    with session_scope() as session:
        question = session.scalar(select(Question).where(Question.external_id == "demo-q8"))
        grade = grade_short_answer(
            question,
            "First prove the initial value, then show truth propagates from n to n plus one.",
            confidence=0.8,
            language="en",
        )

        assert grade is not None
        assert grade.evaluation.score == 0
        assert grade.definitive is False


def test_historical_induction_misgrade_is_repaired_and_mastery_recomputed(db_url):
    with session_scope() as session:
        AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="historical-induction",
            question_id="demo-q8",
            answer="base case and inductive case",
            confidence=1.0,
        )
        attempt = session.scalar(select(Attempt).where(
            Attempt.attempt_id == "historical-induction"
        ))
        attempt.is_correct = False
        attempt.score = 0
        attempt.error_type = "conceptual_misconception"
        attempt.next_action = "show_explanation"
        attempt.grading_details = {"grader": "llm"}

    result = repair(database_url=db_url)
    assert result["repaired_attempts"] == 1
    with session_scope() as session:
        attempt = session.scalar(select(Attempt).where(
            Attempt.attempt_id == "historical-induction"
        ))
        record = session.scalar(select(MasteryRecord).where(
            MasteryRecord.attempt_id == "historical-induction"
        ))
        assert attempt.is_correct is True and attempt.score == 1.0
        assert attempt.grading_details["historically_regraded"] is True
        assert record and record.mastery > 0.25 and record.delta > 0


def test_inconclusive_local_grade_is_pending_without_mastery_penalty(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        concept = session.scalar(select(Concept).where(Concept.slug == "induction"))
        course.external_ai_allowed = False
        question = Question(
            course_id=course.id,
            concept_id=concept.id,
            external_id="explanation-needs-semantic-review",
            prompt="Explain why mathematical induction is valid.",
            expected_answer="It follows from the well-ordering structure of the natural numbers.",
            rubric={},
            question_type="short_answer",
            source_type="ai_generated",
            moderation_status="approved",
        )
        session.add(question)
        session.flush()
        decision = AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="pending-semantic-review",
            question_id=question.external_id,
            answer="Assume the statement holds and prove the next value.",
            confidence=0.8,
        )
        attempt = session.scalar(select(Attempt).where(
            Attempt.attempt_id == "pending-semantic-review"
        ))
        record = session.scalar(select(MasteryRecord).where(
            MasteryRecord.attempt_id == "pending-semantic-review"
        ))
        assert decision.evaluation.next_action == NextAction.retry
        assert decision.evaluation.mastery_delta == 0
        assert attempt.grading_details["grade_status"] == "pending_review"
        assert record and record.mastery == 0.25 and record.delta == 0


def test_missing_prerequisite_selects_review(db_url):
    with session_scope() as session:
        decision = AgentService(session, MissingPrerequisiteProvider()).submit_attempt(
            attempt_id="pre", question_id="demo-q8", answer="unsure", confidence=0.6
        )
        assert decision.evaluation.next_action == NextAction.review_prerequisite
        assert decision.review_concept == "proof-techniques"


def test_no_evidence_never_invents_citation(db_url):
    with session_scope() as session:
        decision = AgentService(session, FakeProvider(), EmptyRetriever()).submit_attempt(
            attempt_id="no-evidence", question_id="demo-q1", answer="wrong", confidence=0.5,
            response_language="zh",
        )
        assert decision.evaluation.evidence == []
        assert "参考答案是" in decision.evaluation.feedback
        assert "AI 基于通用知识生成" not in decision.evaluation.feedback


def test_duplicate_attempt_is_idempotent(db_url):
    with session_scope() as session:
        service = AgentService(session, FakeProvider())
        first = service.submit_attempt(attempt_id="same", question_id="demo-q1", answer="wrong", confidence=0.5)
        second = service.submit_attempt(attempt_id="same", question_id="demo-q1", answer="wrong", confidence=0.5)
        assert first.model_dump() == second.model_dump()
        assert session.scalar(select(func.count()).select_from(Attempt).where(Attempt.attempt_id == "same")) == 1
        assert session.scalar(select(func.count()).select_from(MasteryRecord).where(MasteryRecord.attempt_id == "same")) == 1
        state = session.scalar(select(StudentConceptState))
        assert state and state.attempts_count == 1


def test_every_incorrect_answer_shows_explanation(db_url):
    with session_scope() as session:
        service = AgentService(session, FakeProvider())
        first = service.submit_attempt(
            attempt_id="fail-1", question_id="demo-q1", answer="wrong", confidence=0.6,
            response_language="zh",
        )
        second = service.submit_attempt(
            attempt_id="fail-2", question_id="demo-q1", answer="wrong again", confidence=0.6,
            response_language="zh",
        )
        assert first.evaluation.next_action == NextAction.show_explanation
        assert second.evaluation.next_action == NextAction.show_explanation
        assert "参考答案是" in first.evaluation.feedback


def test_current_concept_state_tracks_streak_confidence_and_review_need(db_url):
    with session_scope() as session:
        service = AgentService(session, FakeProvider())
        service.submit_attempt(
            attempt_id="state-correct", question_id="demo-q7",
            answer="exactly one", confidence=0.9,
        )
        state = session.scalar(select(StudentConceptState))
        assert state
        assert state.correct_streak == 1 and state.failure_streak == 0
        assert state.average_confidence == 0.9

        service.submit_attempt(
            attempt_id="state-wrong", question_id="demo-q7",
            answer="many", confidence=0.5,
        )
        assert state.correct_streak == 0 and state.failure_streak == 1
        assert state.attempts_count == 2
        assert state.last_error_type == "conceptual_misconception"
        assert state.needs_review is True
