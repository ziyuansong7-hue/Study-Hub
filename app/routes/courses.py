from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from app.i18n import translate
from app.routes import bp
from app.routes.general import _active_course
from models import Course
from services.course_service import CourseService
from services.database import session_scope
from services.llm import configured_provider_name


@bp.get("/courses")
def courses():
    with session_scope() as db_session:
        all_courses = list(db_session.scalars(select(Course).order_by(Course.archived_at, Course.title)))
        return render_template(
            "courses.html",
            courses=all_courses,
            active_course=_active_course(db_session),
            provider_name=configured_provider_name(),
        )


def _course_form_values() -> dict:
    return {
        "title": request.form.get("title", ""),
        "code": request.form.get("code", ""),
        "description": request.form.get("description", ""),
        "material_language": request.form.get("material_language", "en"),
        "explanation_language": request.form.get("explanation_language", "en"),
        "terminology_policy": request.form.get("terminology_policy", "preserve_source"),
        "color": request.form.get("color", "#ff9000"),
        "icon": request.form.get("icon", "book"),
        "external_ai_allowed": request.form.get("external_ai_allowed") == "on",
    }


@bp.post("/courses/create")
def create_course():
    try:
        with session_scope() as db_session:
            course = CourseService(db_session).create(**_course_form_values())
            session["active_course_id"] = course.id
        flash(translate("course_created"), "success")
    except ValueError as exc:
        flash(f"{translate('course_error')}: {exc}", "danger")
    return redirect(url_for("main.courses"))


@bp.post("/courses/<int:course_id>/edit")
def edit_course(course_id: int):
    try:
        values = _course_form_values()
        values.pop("code", None)
        with session_scope() as db_session:
            CourseService(db_session).edit(course_id, **values)
        flash(translate("course_updated"), "success")
    except ValueError as exc:
        flash(f"{translate('course_error')}: {exc}", "danger")
    return redirect(url_for("main.courses"))


@bp.post("/courses/<int:course_id>/archive")
def archive_course(course_id: int):
    try:
        with session_scope() as db_session:
            course = db_session.get(Course, course_id)
            if not course:
                raise ValueError("course not found")
            CourseService(db_session).set_archived(course_id, course.archived_at is None)
            if session.get("active_course_id") == course_id:
                session.pop("active_course_id", None)
        flash(translate("course_archived"), "success")
    except ValueError as exc:
        flash(f"{translate('course_error')}: {exc}", "danger")
    return redirect(url_for("main.courses"))


@bp.post("/courses/<int:course_id>/delete")
def delete_course(course_id: int):
    try:
        with session_scope() as db_session:
            course = db_session.get(Course, course_id)
            if not course:
                raise ValueError("course not found")
            if request.form.get("confirmation", "") != course.title:
                raise ValueError("type the exact course name to confirm deletion")
            db_session.delete(course)
            if session.get("active_course_id") == course_id:
                session.pop("active_course_id", None)
        flash(translate("course_deleted"), "success")
    except ValueError as exc:
        flash(f"{translate('course_error')}: {exc}", "danger")
    return redirect(url_for("main.courses"))


@bp.post("/courses/<int:course_id>/select")
def select_course(course_id: int):
    target = request.form.get("next", "/")
    with session_scope() as db_session:
        course = db_session.get(Course, course_id)
        if not course or course.archived_at is not None:
            abort(404)
    session["active_course_id"] = course_id
    return redirect(target if target.startswith("/") and not target.startswith("//") else "/")
