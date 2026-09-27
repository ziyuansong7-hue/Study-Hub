from __future__ import annotations

from flask import Blueprint

bp = Blueprint("main", __name__)

# Import route modules after creating the shared blueprint.
from app.routes import courses as _courses  # noqa: E402,F401
from app.routes import general as _general  # noqa: E402,F401
from app.routes import materials as _materials  # noqa: E402,F401
from app.routes import operations as _operations  # noqa: E402,F401
from app.routes import planning as _planning  # noqa: E402,F401
from app.routes import questions as _questions  # noqa: E402,F401
from app.routes import study as _study  # noqa: E402,F401
