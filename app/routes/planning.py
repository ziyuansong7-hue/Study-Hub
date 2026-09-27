from __future__ import annotations

from datetime import date, datetime, time

from flask import flash, redirect, render_template, request, url_for

from app.i18n import translate
from app.routes import bp
from app.routes.study import _ensure_questions_for_session
from services.database import session_scope
from services.study_plan_service import StudyPlanService
from services.study_session_service import StudySessionService
from services.todo_service import TodoService


def _safe_next(default_endpoint: str, **values) -> str:
    """Return a local redirect target, never a caller-supplied external URL."""
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for(default_endpoint, **values)


def _task_form_values() -> dict:
    try:
        return {
            "course_id": int(request.form["course_id"]),
            "concept_id": int(request.form["concept_id"]) if request.form.get("concept_id") else None,
            "title": request.form.get("title", ""),
            "mode": request.form.get("mode", "focus"),
            "target_type": request.form.get("target_type", "questions"),
            "target_value": int(request.form.get("target_value", "5")),
            "due_date": date.fromisoformat(request.form.get("due_date", date.today().isoformat())),
            "notes": request.form.get("notes", ""),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid task form") from exc


@bp.post("/study-plan/create")
def create_study_task():
    try:
        with session_scope() as db_session:
            StudyPlanService(db_session).create_manual(**_task_form_values())
        flash(translate("task_created"), "success")
    except ValueError as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
    return redirect(_safe_next("main.planner"))


@bp.post("/study-plan/<int:task_id>/edit")
def edit_study_task(task_id: int):
    try:
        with session_scope() as db_session:
            StudyPlanService(db_session).edit(task_id, **_task_form_values())
        flash(translate("task_updated"), "success")
    except ValueError as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
    return redirect(_safe_next("main.planner"))


@bp.post("/study-plan/<int:task_id>/<action>")
def study_task_action(task_id: int, action: str):
    try:
        with session_scope() as db_session:
            StudyPlanService(db_session).action(task_id, action)
        flash(translate(f"task_{action}_done"), "success")
    except ValueError as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
    return redirect(_safe_next("main.planner"))


@bp.post("/study-plan/<int:task_id>/timer/<action>")
def study_task_timer(task_id: int, action: str):
    try:
        with session_scope() as db_session:
            StudyPlanService(db_session).timer_action(task_id, action)
    except ValueError as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
    return redirect(_safe_next("main.planner"))


@bp.get("/study-plan/<int:task_id>/start")
def start_study_task(task_id: int):
    try:
        with session_scope() as db_session:
            task_service = StudyPlanService(db_session)
            task = task_service._task(task_id)
            if task.target_type == "minutes":
                task_service.timer_action(task.id, "start")
            mode = "practice" if task.mode == "focus" and task.concept_id is None else task.mode
            _ensure_questions_for_session(
                db_session,
                course_id=task.course_id,
                concept_ids=[task.concept_id] if task.concept_id else [],
                target_count=task.target_value if task.target_type == "questions" else 5,
                difficulty_level=0,
                question_type="any",
                source_policy="reference_guided",
            )
            study_session = StudySessionService(db_session).create(
                course_id=task.course_id,
                mode=mode,
                concept_id=task.concept_id,
                difficulty=0.5,
                target_count=task.target_value if task.target_type == "questions" else 5,
                question_type="any",
                material_only=False,
                source_policy="reference_guided",
                study_task_id=task.id,
            )
            session_key = study_session.session_key
            external_id = study_session.items[0].question.external_id
        return redirect(url_for(
            "main.practice_session",
            session_key=session_key,
            session_id=session_key,
            question_id=external_id,
        ))
    except ValueError as exc:
        flash(f"{translate('task_error')}: {exc}", "danger")
        return redirect(url_for("main.planner"))


@bp.post("/todos/create")
def create_todo():
    try:
        with session_scope() as db_session:
            TodoService(db_session).create(
                title=request.form.get("title", ""),
                notes=request.form.get("notes", ""),
                due_at=_todo_due_at(),
                priority=request.form.get("priority", "normal"),
                category=request.form.get("category", "personal"),
            )
        flash(translate("todo_created"), "success")
    except (ValueError, TypeError) as exc:
        flash(f"{translate('todo_error')}: {exc}", "danger")
    return redirect(_safe_next("main.planner"))


def _todo_due_at() -> datetime | None:
    raw = request.form.get("due_at", "").strip()
    if raw:
        return datetime.fromisoformat(raw)
    legacy_date = request.form.get("due_date", "").strip()
    if legacy_date:
        return datetime.combine(date.fromisoformat(legacy_date), time(hour=23, minute=59))
    return None


@bp.get("/tasks")
def tasks():
    selected = request.args.get("view", "today")
    if selected not in {"inbox", "overdue", "today", "upcoming", "completed", "all"}:
        selected = "today"
    with session_scope() as db_session:
        groups = TodoService(db_session).grouped()
        return render_template(
            "tasks.html",
            groups=groups,
            selected=selected,
            items=groups[selected],
        )


@bp.post("/todos/<int:item_id>/edit")
def edit_todo(item_id: int):
    try:
        with session_scope() as db_session:
            TodoService(db_session).edit(
                item_id,
                title=request.form.get("title", ""),
                notes=request.form.get("notes", ""),
                due_at=_todo_due_at(),
                priority=request.form.get("priority", "normal"),
                category=request.form.get("category", "personal"),
            )
        flash(translate("todo_updated"), "success")
    except (ValueError, TypeError) as exc:
        flash(f"{translate('todo_error')}: {exc}", "danger")
    return redirect(url_for("main.tasks", view=request.form.get("return_view", "today")))


@bp.post("/todos/<int:item_id>/<action>")
def todo_action(item_id: int, action: str):
    try:
        with session_scope() as db_session:
            TodoService(db_session).action(item_id, action)
    except ValueError as exc:
        flash(f"{translate('todo_error')}: {exc}", "danger")
    return redirect(_safe_next("main.dashboard"))
