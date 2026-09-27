from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from app.security import init_security, load_secret_key
from services.database import configure_database, init_database
from services.logging_config import configure_logging


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    default_db = f"sqlite:///{Path(app.instance_path) / 'studyhub.db'}"
    testing = bool(test_config and test_config.get("TESTING"))
    try:
        max_upload_mb = max(1, int(os.getenv("STUDYHUB_MAX_UPLOAD_MB", "128")))
    except ValueError:
        max_upload_mb = 128
    app.config.from_mapping(
        SECRET_KEY=load_secret_key(app.instance_path, testing=testing),
        DATABASE_URL=os.getenv("STUDYHUB_DATABASE_URL", default_db),
        MAX_CONTENT_LENGTH=max_upload_mb * 1024 * 1024,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)
    init_security(app)

    configure_logging(os.getenv("STUDYHUB_LOG_LEVEL", "INFO"))
    configure_database(app.config["DATABASE_URL"])
    init_database()

    from app.errors import register_error_handlers
    from app.routes import bp

    app.register_blueprint(bp)
    register_error_handlers(app)

    from app.i18n import current_language, translate

    @app.context_processor
    def inject_i18n():
        return {"t": translate, "current_language": current_language()}

    scheduler_enabled = _env_flag("STUDYHUB_ENABLE_SCHEDULER", True)
    debug_reloader_parent = (
        _env_flag("STUDYHUB_DEBUG")
        and os.getenv("WERKZEUG_RUN_MAIN", "").casefold() != "true"
    )
    if not app.config.get("TESTING") and scheduler_enabled and not debug_reloader_parent:
        from services.scheduler import start_scheduler
        start_scheduler(app.config["DATABASE_URL"])
    return app


def main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", debug=_env_flag("STUDYHUB_DEBUG"))
