from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

logger = logging.getLogger(__name__)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(RequestEntityTooLarge)
    def handle_upload_too_large(exc: RequestEntityTooLarge):
        size_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        message = f"Upload exceeds the {size_mb} MiB limit. Increase STUDYHUB_MAX_UPLOAD_MB and restart the app."
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "payload_too_large", "message": message}), 413
        return render_template("error.html", status=413, message=message), 413

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": exc.name.lower().replace(" ", "_"), "message": exc.description}), exc.code
        return render_template("error.html", status=exc.code, message=exc.description), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        logger.exception("unhandled request error")
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "internal_error", "message": "unexpected server error"}), 500
        return render_template("error.html", status=500, message="Unexpected server error"), 500
