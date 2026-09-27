from __future__ import annotations

import os
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select

from models import Concept, Course, ReviewTask
from repositories import LearningRepository
from services.agent_service import AgentService
from services.database import configure_database, init_database, session_scope
from services.retriever import KeywordRetriever


def _ensure_database() -> None:
    configure_database(os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db"))
    init_database()


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _resolve_course(session, course_code: str | None) -> tuple[Course | None, dict | None]:
    if course_code and course_code.strip():
        course = LearningRepository(session).get_course(course_code.strip())
        return (course, None) if course else (None, _error("not_found", f"course not found: {course_code}"))
    courses = list(session.scalars(
        select(Course).where(Course.archived_at.is_(None)).order_by(Course.last_studied_at.desc(), Course.id)
    ))
    if len(courses) == 1:
        return courses[0], None
    if not courses:
        return None, _error("not_found", "no active course found")
    return None, _error("invalid_request", "course_code is required when multiple active courses exist")


def search_course_material(query: str, course_code: str | None = None, limit: int = 5) -> dict:
    if not query.strip():
        return _error("invalid_request", "query must not be empty")
    if not 1 <= limit <= 20:
        return _error("invalid_request", "limit must be between 1 and 20")
    _ensure_database()
    with session_scope() as session:
        course, error = _resolve_course(session, course_code)
        if error:
            return error
        results = KeywordRetriever(session).search(course.id, query, limit)
        return {
            "ok": True,
            "results": [
                {"chunk_id": item.chunk_id, "score": item.score, **item.evidence.model_dump()}
                for item in results
            ],
            "evidence_found": bool(results),
            "message": None if results else "课程材料中未找到可靠依据",
        }


def get_student_state(student_id: str = "default", course_code: str | None = None) -> dict:
    _ensure_database()
    with session_scope() as session:
        repo = LearningRepository(session)
        course, error = _resolve_course(session, course_code)
        if error:
            return error
        concepts = repo.concepts(course.id)
        mastery = [
            {"concept": item.slug, "name": item.name, "mastery": repo.latest_mastery(item.id, student_id)}
            for item in concepts
        ]
        tasks = list(session.scalars(
            select(ReviewTask)
            .join(Concept, Concept.id == ReviewTask.concept_id)
            .where(
                ReviewTask.student_id == student_id,
                ReviewTask.status == "pending",
                Concept.course_id == course.id,
            )
            .order_by(ReviewTask.due_date)
        ))
        return {
            "ok": True,
            "student_id": student_id,
            "course": {"code": course.display_code, "title": course.title},
            "mastery": mastery,
            "review_tasks": [
                {"id": task.id, "concept": task.concept.slug, "due_date": task.due_date.isoformat(), "reason": task.reason}
                for task in tasks
            ],
        }


def save_attempt(
    attempt_id: str,
    question_id: str,
    answer: str,
    confidence: float,
    student_id: str = "default",
    request_explanation: bool = False,
    response_language: str = "en",
    course_code: str | None = None,
) -> dict:
    if not attempt_id.strip() or not answer.strip():
        return _error("invalid_request", "attempt_id and answer are required")
    if not 0 <= confidence <= 1:
        return _error("invalid_request", "confidence must be between 0 and 1")
    _ensure_database()
    try:
        with session_scope() as session:
            course, error = _resolve_course(session, course_code)
            if error:
                return error
            decision = AgentService(session).submit_attempt(
                attempt_id=attempt_id,
                question_id=question_id,
                answer=answer,
                confidence=confidence,
                student_id=student_id,
                request_explanation=request_explanation,
                response_language=response_language,
                course_id=course.id,
            )
            return {"ok": True, "decision": decision.model_dump(mode="json")}
    except (ValueError, ValidationError) as exc:
        return _error("invalid_request", str(exc))
    except Exception as exc:
        return _error("internal_error", f"attempt could not be saved: {type(exc).__name__}")


def schedule_review(
    concept: str,
    due_date: str,
    reason: str,
    student_id: str = "default",
    course_code: str | None = None,
) -> dict:
    try:
        parsed_date = date.fromisoformat(due_date)
    except ValueError:
        return _error("invalid_request", "due_date must use YYYY-MM-DD")
    if not reason.strip():
        return _error("invalid_request", "reason must not be empty")
    _ensure_database()
    with session_scope() as session:
        course, error = _resolve_course(session, course_code)
        if error:
            return error
        concept_row = session.scalar(select(Concept).where(
            Concept.course_id == course.id, Concept.slug == concept
        ))
        if not concept_row:
            return _error("not_found", f"concept not found: {concept}")
        task, created = LearningRepository(session).schedule_review(
            concept_row.id, parsed_date, reason, student_id
        )
        return {
            "ok": True,
            "created": created,
            "review_task": {
                "id": task.id, "concept": concept_row.slug,
                "due_date": task.due_date.isoformat(), "reason": task.reason,
            },
        }
