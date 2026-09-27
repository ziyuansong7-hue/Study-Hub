from __future__ import annotations

from datetime import date

from flask import abort, redirect, render_template, request, session
from sqlalchemy import func, select

from app.i18n import SUPPORTED_LANGUAGES
from app.routes import bp
from models import Attempt, Concept, Course, Document, MasteryRecord, Question, StudentConceptState
from repositories import LearningRepository
from services.course_service import CourseService
from services.database import session_scope
from services.study_plan_service import StudyPlanService
from services.todo_service import TodoService


@bp.post("/language/<language>")
def set_language(language: str):
    if language not in SUPPORTED_LANGUAGES:
        abort(404)
    session["language"] = language
    target = request.form.get("next", "/")
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    return redirect(target)


def _active_course(db_session) -> Course | None:
    courses = CourseService(db_session).list_active()
    if not courses:
        return None
    selected = session.get("active_course_id")
    active = next((course for course in courses if course.id == selected), courses[0])
    session["active_course_id"] = active.id
    return active


def _latest_mastery_rows(db_session, course_id: int, student_id: str = "default") -> list[dict]:
    repo = LearningRepository(db_session)
    rows = [
        {
            "concept": concept,
            "mastery": repo.latest_mastery(concept.id, student_id),
        }
        for concept in repo.concepts(course_id)
    ]
    for row in rows:
        value = row["mastery"]
        row["level"] = "beginner" if value < 0.35 else "developing" if value < 0.6 else "established" if value < 0.8 else "proficient"
    return sorted(rows, key=lambda item: item["mastery"])


def _observed_mastery_rows(db_session, course_id: int, student_id: str = "default") -> list[dict]:
    states = list(db_session.execute(
        select(StudentConceptState, Concept)
        .join(Concept, Concept.id == StudentConceptState.concept_id)
        .where(
            StudentConceptState.student_id == student_id,
            StudentConceptState.attempts_count > 0,
            Concept.course_id == course_id,
        )
        .order_by(StudentConceptState.mastery)
    ))
    return [
        {
            "concept": concept,
            "mastery": state.mastery,
            "level": "beginner" if state.mastery < 0.35 else "developing" if state.mastery < 0.6 else "established" if state.mastery < 0.8 else "proficient",
            "attempts": state.attempts_count,
        }
        for state, concept in states
    ]


@bp.get("/")
def dashboard():
    with session_scope() as db_session:
        plan_service = StudyPlanService(db_session)
        plan_service.ensure_ai_recommendations()
        plan = plan_service.dashboard()
        courses = CourseService(db_session).list_active()
        todo_groups = TodoService(db_session).grouped()
        todos = todo_groups["overdue"] + todo_groups["today"]
        course_cards = []
        for course in courses:
            mastery_rows = _observed_mastery_rows(db_session, course.id)
            course_cards.append({
                "course": course,
                "concept_count": db_session.scalar(select(func.count()).select_from(Concept).where(Concept.course_id == course.id)) or 0,
                "ready_question_count": db_session.scalar(select(func.count()).select_from(Question).where(
                    Question.course_id == course.id,
                    Question.moderation_status == "approved",
                    Question.is_active.is_(True),
                    Question.quality_status == "passed",
                )) or 0,
                "document_count": db_session.scalar(select(func.count()).select_from(Document).where(Document.course_id == course.id)) or 0,
                "attempt_count": db_session.scalar(select(func.count()).select_from(Attempt).join(Question).where(Question.course_id == course.id)) or 0,
                "mastery": (
                    sum(item["mastery"] for item in mastery_rows) / len(mastery_rows)
                    if mastery_rows else 0.0
                ),
                "has_mastery": bool(mastery_rows),
            })
        return render_template(
            "dashboard.html",
            plan=plan,
            courses=courses,
            course_cards=course_cards,
            todos=todos,
            todo_groups=todo_groups,
            today_iso=date.today().isoformat(),
        )


@bp.get("/planner")
def planner():
    with session_scope() as db_session:
        plan_service = StudyPlanService(db_session)
        plan_service.ensure_ai_recommendations()
        courses = CourseService(db_session).list_active()
        concepts = list(db_session.scalars(
            select(Concept).where(Concept.course_id.in_([course.id for course in courses] or [-1]))
            .order_by(Concept.course_id, Concept.name)
        ))
        todo_groups = TodoService(db_session).grouped()
        return render_template(
            "planner.html",
            plan=plan_service.dashboard(),
            courses=courses,
            concepts=concepts,
            todos=todo_groups["overdue"] + todo_groups["today"],
            todo_groups=todo_groups,
            today_iso=date.today().isoformat(),
        )


@bp.get("/courses/<int:course_id>/overview")
def course_overview(course_id: int):
    with session_scope() as db_session:
        course = db_session.get(Course, course_id)
        if not course or course.archived_at is not None:
            abort(404)
        mastery_rows = _observed_mastery_rows(db_session, course.id)
        concepts = list(db_session.scalars(
            select(Concept).where(Concept.course_id == course.id).order_by(Concept.chapter, Concept.name)
        ))
        ready_counts = dict(db_session.execute(
            select(Question.concept_id, func.count(Question.id)).where(
                Question.course_id == course.id,
                Question.moderation_status == "approved",
                Question.is_active.is_(True),
                Question.quality_status == "passed",
            ).group_by(Question.concept_id)
        ).all())
        documents = list(db_session.scalars(
            select(Document).where(Document.course_id == course.id).order_by(Document.created_at.desc())
        ))
        recent = list(db_session.scalars(
            select(MasteryRecord).join(Concept).where(
                MasteryRecord.student_id == "default", Concept.course_id == course.id
            ).order_by(MasteryRecord.created_at.desc()).limit(8)
        ))
        return render_template(
            "course_overview.html",
            course=course,
            concepts=concepts,
            ready_counts=ready_counts,
            ready_total=sum(ready_counts.values()),
            mastery_rows=mastery_rows,
            documents=documents,
            recent=recent,
            course_plan=StudyPlanService(db_session).dashboard(course.id),
        )
