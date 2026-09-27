from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import select

from models import AgentRun, Attempt, Course, MasteryRecord, Question, StudentConceptState
from schemas import AgentDecision
from scripts.seed_demo import DEMO_RUBRICS
from services.answer_grading import grade_short_answer
from services.database import configure_database, session_scope
from services.mastery import mastery_update

load_dotenv()


def _recompute_concept_state(session, concept_id: int, student_id: str) -> None:
    attempts = list(session.scalars(
        select(Attempt)
        .join(Question, Question.id == Attempt.question_id)
        .where(Question.concept_id == concept_id, Attempt.student_id == student_id)
        .order_by(Attempt.created_at, Attempt.id)
    ))
    mastery = 0.25
    correct_streak = 0
    failure_streak = 0
    confidence_total = 0.0
    for attempt in attempts:
        details = attempt.grading_details or {}
        if details.get("grade_status") == "pending_review":
            updated, delta = mastery, 0.0
        else:
            updated, delta = mastery_update(
                mastery,
                score=attempt.score,
                confidence=attempt.confidence,
                is_correct=attempt.is_correct,
            )
            if attempt.is_correct:
                correct_streak += 1
                failure_streak = 0
            else:
                correct_streak = 0
                failure_streak += 1
        record = session.scalar(select(MasteryRecord).where(
            MasteryRecord.attempt_id == attempt.attempt_id
        ))
        if record:
            record.mastery = updated
            record.delta = delta
        details = dict(attempt.grading_details or {})
        saved_decision = dict(details.get("decision") or {})
        saved_evaluation = dict(saved_decision.get("evaluation") or {})
        if saved_evaluation:
            saved_evaluation["mastery_delta"] = delta
            saved_decision["evaluation"] = saved_evaluation
            details["decision"] = saved_decision
            attempt.grading_details = details
        mastery = updated
        confidence_total += attempt.confidence

    state = session.scalar(select(StudentConceptState).where(
        StudentConceptState.student_id == student_id,
        StudentConceptState.concept_id == concept_id,
    ))
    if not state:
        state = StudentConceptState(student_id=student_id, concept_id=concept_id)
        session.add(state)
    state.mastery = mastery
    state.attempts_count = len(attempts)
    state.average_confidence = confidence_total / len(attempts) if attempts else 0.0
    state.correct_streak = correct_streak
    state.failure_streak = failure_streak
    if attempts:
        latest = attempts[-1]
        state.last_practiced_at = latest.created_at
        state.last_error_type = latest.error_type
        state.needs_review = mastery < 0.55 or latest.next_action in {
            "review_prerequisite", "schedule_review"
        }


def repair(*, database_url: str, enable_external_ai_for: str | None = None) -> dict[str, object]:
    configure_database(database_url)
    repaired_ids: list[str] = []
    touched_concepts: set[tuple[int, str]] = set()
    with session_scope() as session:
        question = session.scalar(select(Question).where(Question.external_id == "demo-q8"))
        if question:
            question.rubric = DEMO_RUBRICS["demo-q8"]
            attempts = list(session.scalars(
                select(Attempt)
                .where(Attempt.question_id == question.id, Attempt.is_correct.is_(False))
                .order_by(Attempt.id)
            ))
            for attempt in attempts:
                local_grade = grade_short_answer(
                    question, attempt.answer, attempt.confidence,
                    "zh" if question.course.explanation_language == "zh" else "en",
                )
                if not local_grade or not local_grade.evaluation.is_correct:
                    continue
                evaluation = local_grade.evaluation
                decision = AgentDecision(
                    primary_gap=question.concept.name,
                    rationale_summary=f"Historical regrade using {local_grade.grader}.",
                    evaluation=evaluation,
                )
                attempt.is_correct = True
                attempt.score = evaluation.score
                attempt.error_type = evaluation.error_type.value
                attempt.feedback = evaluation.feedback
                attempt.next_action = evaluation.next_action.value
                attempt.evidence = []
                attempt.grading_details = {
                    "decision": decision.model_dump(mode="json"),
                    "conclusion_correct": evaluation.conclusion_correct,
                    "key_steps_score": evaluation.key_steps_score,
                    "explanation_score": evaluation.explanation_score,
                    "fatal_error": evaluation.fatal_error,
                    "grader": local_grade.grader,
                    "grade_status": "graded",
                    "configured_provider": os.getenv("STUDYHUB_LLM_PROVIDER", "fake"),
                    "course_external_ai_allowed": bool(question.course.external_ai_allowed),
                    "historically_regraded": True,
                }
                repaired_ids.append(attempt.attempt_id)
                touched_concepts.add((question.concept_id, attempt.student_id))

        if repaired_ids:
            for run in session.scalars(select(AgentRun).where(AgentRun.operation == "study_attempt")):
                if (run.input_summary or {}).get("attempt_id") in repaired_ids:
                    attempt = session.scalar(select(Attempt).where(
                        Attempt.attempt_id == run.input_summary["attempt_id"]
                    ))
                    run.decision = (attempt.grading_details or {}).get("decision", {})
                    run.tools_used = ["search_course_material", "grade:local_key_concepts", "historical_regrade"]
                    run.status = "success"
                    run.error = None

        for concept_id, student_id in touched_concepts:
            _recompute_concept_state(session, concept_id, student_id)

        enabled_course = None
        if enable_external_ai_for:
            course = session.scalar(select(Course).where(Course.code == enable_external_ai_for))
            if not course:
                raise ValueError(f"course not found: {enable_external_ai_for}")
            course.external_ai_allowed = True
            enabled_course = course.code

    return {
        "repaired_attempts": len(repaired_ids),
        "attempt_ids": repaired_ids,
        "external_ai_enabled_for": enabled_course,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db"),
    )
    parser.add_argument("--enable-external-ai-for")
    args = parser.parse_args()
    print(repair(
        database_url=args.database_url,
        enable_external_ai_for=args.enable_external_ai_for,
    ))
