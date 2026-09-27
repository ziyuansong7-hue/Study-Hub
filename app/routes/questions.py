from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.i18n import translate
from app.routes import bp
from app.routes.general import _active_course
from models import Concept, Question
from services.content_service import (
    ConceptDiscoveryService,
    QuestionGenerationService,
    QuestionModerationService,
)
from services.course_service import CourseService
from services.database import session_scope
from services.llm import configured_provider_name


@bp.get("/questions")
def questions():
    with session_scope() as db_session:
        courses = CourseService(db_session).list_active()
        active_course = _active_course(db_session)
        course_id = request.args.get("course_id", type=int) or (active_course.id if active_course else None)
        status = request.args.get("status", "pending")
        query = select(Question).order_by(Question.created_at.desc())
        if course_id:
            query = query.where(Question.course_id == course_id)
        if status in {"pending", "approved", "rejected"}:
            query = query.where(Question.moderation_status == status)
        question_rows = list(db_session.scalars(query))
        concepts = list(db_session.scalars(
            select(Concept).where(Concept.course_id == (course_id or -1)).order_by(Concept.name)
        ))
        return render_template(
            "questions.html",
            questions=question_rows,
            courses=courses,
            concepts=concepts,
            selected_course_id=course_id,
            selected_status=status,
            configured_provider=configured_provider_name(),
            selected_course=next((value for value in courses if value.id == course_id), None),
        )


@bp.post("/questions/generate")
def generate_questions():
    course_id = request.form.get("course_id", type=int)
    try:
        if not course_id:
            raise ValueError("course is required")
        with session_scope() as db_session:
            source_policy = request.form.get("source_policy") or (
                "material_strict" if request.form.get("material_only") == "on" else "reference_guided"
            )
            created = QuestionGenerationService(db_session).generate(
                course_id=course_id,
                concept_id=int(request.form["concept_id"]),
                count=int(request.form.get("count", "3")),
                difficulty=(int(request.form.get("difficulty_level", "3")) - 1) / 4,
                target_level=int(request.form.get("difficulty_level", "3")),
                question_type=request.form.get("question_type", "short_answer"),
                material_only=source_policy == "material_strict",
                source_policy=source_policy,
            )
        flash(translate("questions_generated", count=len(created)), "success")
    except (KeyError, TypeError, ValueError) as exc:
        flash(f"{translate('question_generation_error')}: {exc}", "danger")
    return redirect(url_for("main.questions", course_id=course_id, status="pending"))


@bp.post("/questions/<int:question_id>/<action>")
def question_action(question_id: int, action: str):
    try:
        with session_scope() as db_session:
            question = QuestionModerationService(db_session).action(question_id, action)
            course_id = question.course_id
        flash(translate("question_updated"), "success")
    except ValueError as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.questions", course_id=course_id, status=request.form.get("return_status", "pending")))


@bp.post("/questions/<int:question_id>/edit")
def edit_question(question_id: int):
    try:
        with session_scope() as db_session:
            question = QuestionModerationService(db_session).edit(
                question_id,
                prompt=request.form.get("prompt", ""),
                expected_answer=request.form.get("expected_answer", ""),
                question_type=request.form.get("question_type", "short_answer"),
                options=request.form.get("options", ""),
                expected_minutes=request.form.get("expected_minutes", "5"),
                review_notes=request.form.get("review_notes", ""),
            )
            course_id = question.course_id
        flash(translate("question_updated"), "success")
    except (TypeError, ValueError) as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.questions", course_id=course_id, status="pending"))


@bp.post("/questions/<int:question_id>/rewrite")
def rewrite_question(question_id: int):
    try:
        with session_scope() as db_session:
            original = QuestionModerationService(db_session).get(question_id)
            original.moderation_status = "rejected"
            original.is_active = False
            target_level = max(1, min(5, original.difficulty_level + int(request.form.get("level_delta", "0"))))
            created = QuestionGenerationService(db_session).generate(
                course_id=original.course_id,
                concept_id=original.concept_id,
                count=1,
                difficulty=(target_level - 1) / 4,
                target_level=target_level,
                question_type=original.question_type,
                material_only=original.source_policy == "material_strict",
                source_policy=original.source_policy,
                review_feedback=request.form.get("feedback", "Create a distinct replacement and address ambiguity."),
            )
            course_id = original.course_id
        flash(translate("questions_generated", count=len(created)), "success")
    except ValueError as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.questions", course_id=course_id, status="pending"))


@bp.post("/concept-candidates/<int:candidate_id>/<action>")
def concept_candidate_action(candidate_id: int, action: str):
    try:
        with session_scope() as db_session:
            candidate = ConceptDiscoveryService(db_session).decide(
                candidate_id,
                action=action,
                name=request.form.get("name", ""),
                merge_concept_id=request.form.get("merge_concept_id", type=int),
            )
            course_id = candidate.document.course_id
            document_id = candidate.document_id
            status = candidate.status
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=True, candidate_id=candidate_id, status=status)
        flash(translate("concept_updated"), "success")
    except ValueError as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=False, error={"code": "invalid_request", "message": str(exc)}), 400
        course_id = None
        document_id = None
        flash(f"{translate('concept_error')}: {exc}", "danger")
    return redirect(url_for("main.concepts", course_id=course_id, document_id=document_id))
