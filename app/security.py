from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session
from markupsafe import Markup

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def load_secret_key(instance_path: str, *, testing: bool = False) -> str:
    """Return an explicit key or persist a private local key for stable sessions."""
    configured = os.getenv("STUDYHUB_SECRET_KEY", "").strip()
    if configured:
        return configured
    if testing:
        return "studyhub-test-secret"

    secret_path = Path(instance_path) / ".secret_key"
    if secret_path.exists():
        value = secret_path.read_text(encoding="utf-8").strip()
        if value:
            return value

    value = secrets.token_urlsafe(48)
    secret_path.write_text(value, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return value


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_field() -> Markup:
    token = _csrf_token()
    return Markup(f'<input type="hidden" name="_csrf_token" value="{token}">')


def init_security(app: Flask) -> None:
    """Apply local-app session defaults and CSRF protection for browser forms."""
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Strict")
    app.config.setdefault("CSRF_ENABLED", not app.config.get("TESTING", False))

    @app.before_request
    def protect_browser_forms():
        if (
            not app.config.get("CSRF_ENABLED", True)
            or request.method in SAFE_METHODS
            or request.is_json
        ):
            return None
        expected = session.get("_csrf_token", "")
        submitted = request.form.get("_csrf_token", "") or request.headers.get(
            "X-CSRF-Token", ""
        )
        if not expected or not submitted or not hmac.compare_digest(expected, submitted):
            abort(400, description="Invalid or missing CSRF token.")
        return None

    app.jinja_env.globals["csrf_token"] = _csrf_token
    app.jinja_env.globals["csrf_field"] = _csrf_field
