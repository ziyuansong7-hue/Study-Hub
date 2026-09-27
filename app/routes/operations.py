from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from flask import (
    after_this_request,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import select

from app.i18n import current_language, translate
from app.routes import bp
from models import AgentRun
from services.agent_service import AgentService
from services.data_management import create_backup, restore_backup
from services.database import session_scope


@bp.get("/agent-runs")
def agent_runs():
    with session_scope() as db_session:
        runs = list(db_session.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(100)))
        return render_template("agent_runs.html", runs=runs)


@bp.get("/data")
def data_management():
    return render_template("data.html")


@bp.get("/data/backup")
def download_backup():
    backup = create_backup(current_app.config["DATABASE_URL"])

    @after_this_request
    def remove_temporary(response):
        backup.unlink(missing_ok=True)
        return response

    return send_file(
        backup,
        as_attachment=True,
        download_name=f"studyhub-backup-{datetime.now():%Y%m%d-%H%M%S}.sqlite3",
    )


@bp.post("/data/restore")
def restore_data():
    uploaded = request.files.get("backup")
    if not uploaded or request.form.get("confirmation") != "RESTORE":
        flash(translate("restore_confirmation_required"), "danger")
        return redirect(url_for("main.data_management"))
    descriptor, temporary_name = tempfile.mkstemp(prefix="studyhub-restore-", suffix=".sqlite3")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        uploaded.save(temporary)
        safety = restore_backup(current_app.config["DATABASE_URL"], temporary)
        flash(translate("restore_complete", path=str(safety)), "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    finally:
        temporary.unlink(missing_ok=True)
    return redirect(url_for("main.data_management"))


@bp.post("/api/attempts")
def api_attempt():
    payload = request.get_json(silent=True) or {}
    required = {"attempt_id", "question_id", "answer", "confidence"}
    missing = sorted(required - payload.keys())
    if missing:
        return jsonify(ok=False, error={"code": "invalid_request", "message": f"missing: {', '.join(missing)}"}), 400
    try:
        with session_scope() as db_session:
            decision = AgentService(db_session).submit_attempt(
                attempt_id=str(payload["attempt_id"]),
                question_id=str(payload["question_id"]),
                answer=str(payload["answer"]),
                confidence=float(payload["confidence"]),
                request_explanation=True,
                response_language=str(payload.get("response_language", current_language())),
                course_id=int(payload["course_id"]) if payload.get("course_id") is not None else None,
            )
            return jsonify(ok=True, decision=decision.model_dump(mode="json"))
    except ValueError as exc:
        return jsonify(ok=False, error={"code": "invalid_request", "message": str(exc)}), 400


@bp.get("/api/health")
def api_health():
    return jsonify(ok=True, service="studyhub")
