from __future__ import annotations

import uuid
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from app.i18n import current_language, translate
from app.routes import bp
from app.routes.general import _active_course
from models import Attempt, Concept, PracticeScopePreset, Question, ReviewTask, StudentConceptState
from services.agent_service import AgentService
from services.content_service import QuestionGenerationService
from services.course_service import CourseService
from services.database import session_scope
from services.llm import configured_provider_name
from services.study_plan_service import StudyPlanService
from services.study_session_service import StudySessionService


def _find_question(db_session, external_id: str | None = None, course_id: int | None = None) -> Question:
    query = select(Question).where(
        Question.is_active.is_(True),
        Question.moderation_status == "approved",
        Question.quality_status == "passed",
    )
    if external_id:
        query = query.where(Question.external_id == external_id)
    if course_id:
        query = query.where(Question.course_id == course_id)
    question = db_session.scalar(query.order_by(Question.id).limit(1))
    if not question:
        abort(404, "No questions are available. Import material or run the demo seed first.")
    return question


def _study_context(db_session, decision=None, submitted_question=None, session_key=None):
    courses = CourseService(db_session).list_active()
    requested_course_id = request.values.get("course_id", type=int)
    active_course = next((value for value in courses if value.id == requested_course_id), None)
    if not active_course:
        active_course = _active_course(db_session)
    concepts = list(db_session.scalars(
        select(Concept)
        .where(Concept.course_id.in_([course.id for course in courses] or [-1]))
        .order_by(Concept.course_id, Concept.name)
    ))
    presets = list(db_session.scalars(
        select(PracticeScopePreset)
        .where(PracticeScopePreset.course_id.in_([course.id for course in courses] or [-1]))
        .order_by(PracticeScopePreset.name)
    ))
    weak_concept_ids = set(db_session.scalars(
        select(StudentConceptState.concept_id).where(
            StudentConceptState.student_id == "default",
            StudentConceptState.mastery < 0.55,
        )
    ))
    due_concept_ids = set(db_session.scalars(
        select(ReviewTask.concept_id).where(
            ReviewTask.student_id == "default",
            ReviewTask.status == "pending",
            ReviewTask.due_date <= date.today(),
        )
    ))
    ready_counts = dict(db_session.execute(
        select(Question.concept_id, func.count(Question.id)).where(
            Question.is_active.is_(True),
            Question.moderation_status == "approved",
            Question.quality_status == "passed",
        ).group_by(Question.concept_id)
    ).all())
    study_session = None
    session_item = None
    next_item = None
    question = submitted_question
    session_key = session_key or request.values.get("session_id")
    if session_key:
        study_session = StudySessionService(db_session).get(session_key)
        retry_question_id = request.args.get("retry_question_id")
        if submitted_question is None and retry_question_id:
            question = _find_question(db_session, retry_question_id, study_session.course_id)
        elif submitted_question is None:
            session_item = StudySessionService.current_item(study_session)
            question = session_item.question if session_item else None
        else:
            item_id = request.form.get("session_item_id", type=int)
            session_item = next((item for item in study_session.items if item.id == item_id), None)
        next_item = StudySessionService.current_item(study_session)
    elif question is None and request.values.get("question_id"):
        question = _find_question(
            db_session,
            request.values.get("question_id"),
            request.values.get("course_id", type=int) or (active_course.id if active_course else None),
        )
    elif question is None and active_course:
        # Keep one safe, course-scoped preview available on the landing page.
        # Drafts and quarantined questions never reach this preview.
        question = db_session.scalar(
            select(Question).where(
                Question.course_id == active_course.id,
                Question.is_active.is_(True),
                Question.moderation_status == "approved",
                Question.quality_status == "passed",
            ).order_by(Question.id).limit(1)
        )
    hint = None
    if request.args.get("hint") == "1" and question:
        hint_language = question.course.explanation_language
        if hint_language == "mixed":
            hint_language = current_language()
        hint = (
            f"先回到 {question.concept.name} 的定义，找出题目要求的一个关键条件。"
            if hint_language == "zh"
            else f"Return to the definition of {question.concept.name} and identify the one key condition requested."
        )
    session_concepts = []
    if study_session:
        selected_ids = study_session.concept_ids or (
            [study_session.concept_id] if study_session.concept_id else []
        )
        session_concepts = [value for value in concepts if value.id in selected_ids]
    effective_grader = AgentService.planned_grader(question) if question else None
    grade_status = "graded"
    if decision:
        attempt_id = request.form.get("attempt_id")
        saved_attempt = db_session.scalar(
            select(Attempt).where(Attempt.attempt_id == attempt_id)
        ) if attempt_id else None
        if saved_attempt:
            details = saved_attempt.grading_details or {}
            effective_grader = details.get("grader", effective_grader)
            grade_status = details.get("grade_status", "graded")
    return {
        "courses": courses,
        "active_course": active_course,
        "concepts": concepts,
        "practice_presets": presets,
        "weak_concept_ids": weak_concept_ids,
        "due_concept_ids": due_concept_ids,
        "ready_counts": ready_counts,
        "configured_provider": configured_provider_name(),
        "question": question,
        "study_session": study_session,
        "session_concepts": session_concepts,
        "session_item": session_item,
        "next_item": next_item,
        "attempt_id": str(uuid.uuid4()),
        "decision": decision,
        "hint": hint,
        "effective_grader": effective_grader,
        "grade_status": grade_status,
    }


def _ensure_questions_for_session(
    db_session,
    *,
    course_id: int,
    concept_ids: list[int],
    target_count: int,
    difficulty_level: int,
    question_type: str,
    source_policy: str,
) -> int:
    query = select(func.count()).select_from(Question).where(
        Question.course_id == course_id,
        Question.is_active.is_(True),
        Question.moderation_status == "approved",
        Question.quality_status == "passed",
    )
    if concept_ids:
        query = query.where(Question.concept_id.in_(concept_ids))
    if question_type != "any":
        query = query.where(Question.question_type == question_type)
    if source_policy != "any":
        query = query.where(Question.source_policy == source_policy)
    available = db_session.scalar(query) or 0
    needed = max(0, target_count - available)
    if needed == 0:
        return 0
    # A clean draft may already exist from the review workflow. Promote only
    # the number currently missing; an existing ready bank must not silently
    # activate unrelated drafts. Quarantined questions never enter study.
    draft_query = select(Question).where(
        Question.course_id == course_id,
        Question.moderation_status == "pending",
        Question.quality_status == "passed",
    )
    if concept_ids:
        draft_query = draft_query.where(Question.concept_id.in_(concept_ids))
    if question_type != "any":
        draft_query = draft_query.where(Question.question_type == question_type)
    if source_policy != "any":
        draft_query = draft_query.where(Question.source_policy == source_policy)
    promoted = 0
    for draft in db_session.scalars(draft_query.order_by(Question.id).limit(needed)):
        draft.moderation_status = "approved"
        draft.is_active = True
        draft.review_notes = "Auto-approved after the deterministic quality gate for this local session."
        promoted += 1
        needed -= 1
    if needed == 0:
        db_session.flush()
        return promoted
    scope_query = select(Concept).where(Concept.course_id == course_id)
    if concept_ids:
        scope_query = scope_query.where(Concept.id.in_(concept_ids))
    scope = list(db_session.scalars(
        scope_query.order_by((Concept.importance == "core").desc(), Concept.id)
        .limit(max(1, min(5, len(concept_ids) if concept_ids else 3)))
    ))
    approved = promoted
    for index, concept in enumerate(scope):
        if needed <= 0:
            break
        remaining_concepts = max(1, len(scope) - index)
        request_count = max(1, min(10, (needed + remaining_concepts - 1) // remaining_concepts))
        try:
            created = QuestionGenerationService(db_session).generate(
                course_id=course_id,
                concept_id=concept.id,
                count=request_count,
                difficulty=(max(1, difficulty_level or 3) - 1) / 4,
                target_level=difficulty_level or 3,
                question_type=question_type,
                material_only=source_policy == "material_strict",
                source_policy=source_policy,
            )
        except ValueError:
            continue
        for question in created:
            if question.quality_status == "passed":
                question.moderation_status = "approved"
                question.is_active = True
                question.review_notes = "Auto-approved after the deterministic quality gate for this local session."
                approved += 1
                needed -= 1
    db_session.flush()
    return approved


@bp.post("/study/start")
def start_study_session():
    try:
        with session_scope() as db_session:
            selected_concept_ids = [
                int(value) for value in request.form.getlist("concept_ids") if value
            ]
            legacy_concept_id = request.form.get("concept_id", type=int)
            if legacy_concept_id and legacy_concept_id not in selected_concept_ids:
                selected_concept_ids.append(legacy_concept_id)
            course_id = int(request.form["course_id"])
            mode = request.form.get("mode", "practice")
            difficulty_level = int(request.form.get("difficulty_level", "0"))
            target_count = int(request.form.get("target_count", "5"))
            question_type = request.form.get("question_type", "any")
            source_policy = request.form.get("source_policy") or (
                "material_strict" if request.form.get("material_only") == "on" else "reference_guided"
            )
            material_only = source_policy == "material_strict"
            preset_name = request.form.get("preset_name", "").strip()[:120]
            if preset_name and mode == "practice":
                preset = db_session.scalar(select(PracticeScopePreset).where(
                    PracticeScopePreset.student_id == "default",
                    PracticeScopePreset.course_id == course_id,
                    PracticeScopePreset.name == preset_name,
                ))
                if not preset:
                    preset = PracticeScopePreset(
                        student_id="default", course_id=course_id, name=preset_name
                    )
                    db_session.add(preset)
                preset.concept_ids = selected_concept_ids
                preset.difficulty_level = difficulty_level
                preset.target_count = target_count
                preset.question_type = question_type
                preset.material_only = material_only
            _ensure_questions_for_session(
                db_session,
                course_id=course_id,
                concept_ids=selected_concept_ids,
                target_count=target_count,
                difficulty_level=difficulty_level,
                question_type=question_type,
                source_policy=source_policy,
            )
            study_session = StudySessionService(db_session).create(
                course_id=course_id,
                mode=mode,
                concept_id=None,
                concept_ids=selected_concept_ids,
                difficulty=float(request.form.get("difficulty", "0.5")),
                difficulty_level=difficulty_level,
                target_count=target_count,
                question_type=question_type,
                material_only=material_only,
                source_policy=source_policy,
            )
            key = study_session.session_key
        return redirect(url_for("main.practice_session", session_key=key, session_id=key))
    except (KeyError, TypeError, ValueError) as exc:
        flash(f"{translate('session_error')}: {exc}", "danger")
        return redirect(url_for("main.study"))


@bp.post("/study/presets/<int:preset_id>/delete")
def delete_practice_preset(preset_id: int):
    with session_scope() as db_session:
        preset = db_session.get(PracticeScopePreset, preset_id)
        if not preset or preset.student_id != "default":
            abort(404)
        db_session.delete(preset)
    flash(translate("practice_preset_deleted"), "success")
    return redirect(url_for("main.study"))


@bp.post("/study/session/<session_key>/skip")
def skip_study_question(session_key: str):
    try:
        with session_scope() as db_session:
            service = StudySessionService(db_session)
            study_session = service.get(session_key)
            service.skip_current(study_session)
        flash(translate("question_skipped"), "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("main.practice_session", session_key=session_key))


@bp.post("/study/prerequisite")
def start_prerequisite_session():
    parent_key = request.form.get("parent_session_id", "")
    try:
        with session_scope() as db_session:
            service = StudySessionService(db_session)
            parent = service.get(parent_key)
            concept = db_session.scalar(select(Concept).where(
                Concept.course_id == parent.course_id,
                Concept.slug == request.form.get("concept_slug", ""),
            ))
            if not concept:
                raise ValueError("prerequisite concept not found")
            child = service.create(
                course_id=parent.course_id,
                mode="focus",
                concept_id=concept.id,
                difficulty=0.5,
                difficulty_level=0,
                target_count=3,
                question_type="any",
                material_only=parent.material_only,
                parent_session_id=parent.id,
            )
            parent.status = "suspended"
            child_key = child.session_key
        return redirect(url_for("main.practice_session", session_key=child_key))
    except ValueError as exc:
        flash(f"{translate('session_error')}: {exc}", "danger")
        return redirect(url_for("main.practice_session", session_key=parent_key))


@bp.post("/study/session/<session_key>/resume")
def resume_study_session(session_key: str):
    try:
        with session_scope() as db_session:
            study_session = StudySessionService(db_session).get(session_key)
            study_session.status = "active"
        return redirect(url_for("main.practice_session", session_key=session_key))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.study"))


@bp.post("/study/schedule-review")
def schedule_study_review():
    try:
        with session_scope() as db_session:
            _task, created = StudyPlanService(db_session).schedule_concept_review(
                int(request.form["concept_id"])
            )
        flash(translate("review_scheduled" if created else "review_already_scheduled"), "success")
    except (KeyError, TypeError, ValueError) as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
    return redirect(url_for("main.dashboard"))


def _submit_study_answer(db_session, session_key: str | None = None):
    decision = None
    submitted_question = None
    submitted_question = _find_question(
        db_session,
        request.form.get("question_id"),
        request.form.get("course_id", type=int),
    )
    answers = request.form.getlist("answer")
    answer = ", ".join(value.strip() for value in answers if value.strip())
    if not answer:
        flash(translate("answer_required"), "danger")
        return decision, submitted_question
    attempt_id = request.form.get("attempt_id") or str(uuid.uuid4())
    decision = AgentService(db_session).submit_attempt(
        attempt_id=attempt_id,
        question_id=submitted_question.external_id,
        answer=answer,
        confidence=float(request.form.get("confidence", "0.5")),
        request_explanation=True,
        response_language=(
            submitted_question.course.explanation_language
            if submitted_question.course.explanation_language in {"en", "zh"}
            else current_language()
        ),
        course_id=submitted_question.course_id,
    )
    active_session_key = session_key or request.form.get("session_id")
    item_id = request.form.get("session_item_id", type=int)
    if active_session_key and item_id:
        study_session = StudySessionService(db_session).get(active_session_key)
        item = next((value for value in study_session.items if value.id == item_id), None)
        if item and item.question_id == submitted_question.id:
            StudySessionService(db_session).record_attempt(item, attempt_id)
    return decision, submitted_question


@bp.route("/study/session/<session_key>", methods=["GET", "POST"])
def practice_session(session_key: str):
    decision = None
    submitted_question = None
    with session_scope() as db_session:
        if request.method == "POST":
            decision, submitted_question = _submit_study_answer(db_session, session_key)
        context = _study_context(
            db_session,
            decision=decision,
            submitted_question=submitted_question,
            session_key=session_key,
        )
        return render_template("practice.html", **context)


@bp.route("/study", methods=["GET", "POST"])
def study():
    decision = None
    submitted_question = None
    with session_scope() as db_session:
        if request.method == "POST":
            # Backward compatibility for bookmarks and older clients that submit here.
            decision, submitted_question = _submit_study_answer(db_session)
        context = _study_context(db_session, decision=decision, submitted_question=submitted_question)
        if request.method == "POST" and context["study_session"]:
            return render_template("practice.html", **context)
        return render_template("study.html", **context)


@bp.get("/mistakes")
def mistakes():
    concept = request.args.get("concept", "")
    error_type = request.args.get("error_type", "")
    course_id = request.args.get("course_id", type=int)
    with session_scope() as db_session:
        active_course = _active_course(db_session)
        course_id = course_id or (active_course.id if active_course else None)
        query = select(Attempt).where(Attempt.is_correct.is_(False)).join(Question)
        if course_id:
            query = query.where(Question.course_id == course_id)
        if concept:
            query = query.join(Concept).where(Concept.slug == concept)
        if error_type:
            query = query.where(Attempt.error_type == error_type)
        attempts = list(db_session.scalars(query.order_by(Attempt.created_at.desc())))
        concepts_query = select(Concept)
        if course_id:
            concepts_query = concepts_query.where(Concept.course_id == course_id)
        concepts = list(db_session.scalars(concepts_query.order_by(Concept.name)))
        courses = CourseService(db_session).list_active()
        return render_template(
            "mistakes.html",
            attempts=attempts,
            concepts=concepts,
            courses=courses,
            selected_course_id=course_id,
            selected_concept=concept,
            selected_error=error_type,
        )
