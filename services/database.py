from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import Lock
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from models import Base

_lock = Lock()
_engine: Engine | None = None
_session_factory: scoped_session | None = None


def _normalize_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:///") or url.endswith(":memory:"):
        return url
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def configure_database(url: str) -> Engine:
    global _engine, _session_factory
    normalized = _normalize_sqlite_url(url)
    with _lock:
        if _engine is not None:
            _engine.dispose()
        connect_args = {"check_same_thread": False} if normalized.startswith("sqlite") else {}
        _engine = create_engine(normalized, future=True, connect_args=connect_args)
        if normalized.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        _session_factory = scoped_session(
            sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        )
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_database("sqlite:///instance/studyhub.db")
    assert _engine is not None
    return _engine


def init_database() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    apply_sqlite_compatibility(engine)


def apply_sqlite_compatibility(engine: Engine | Connection) -> None:
    """Apply small, repeatable additive migrations for existing local MVP databases."""
    if engine.dialect.name != "sqlite":
        return
    additions: dict[str, dict[str, str]] = {
        "courses": {
            "code_is_generated": "BOOLEAN NOT NULL DEFAULT 0",
            "material_language": "VARCHAR(16) NOT NULL DEFAULT 'en'",
            "explanation_language": "VARCHAR(16) NOT NULL DEFAULT 'en'",
            "terminology_policy": "VARCHAR(32) NOT NULL DEFAULT 'preserve_source'",
            "color": "VARCHAR(16) NOT NULL DEFAULT '#ff9000'",
            "icon": "VARCHAR(32) NOT NULL DEFAULT 'book'",
            "archived_at": "DATETIME",
            "last_studied_at": "DATETIME",
            "external_ai_allowed": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "documents": {
            "source_category": "VARCHAR(32) NOT NULL DEFAULT 'course_material'",
            "source_year": "INTEGER",
            "original_content": "BLOB",
            "material_type": "VARCHAR(32) NOT NULL DEFAULT 'auto'",
            "material_type_source": "VARCHAR(16) NOT NULL DEFAULT 'auto'",
            "detected_material_type": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
            "analysis_mode": "VARCHAR(32) NOT NULL DEFAULT 'quick'",
            "analysis_status": "VARCHAR(32) NOT NULL DEFAULT 'not_started'",
            "analysis_provider": "VARCHAR(64) NOT NULL DEFAULT 'local'",
            "analysis_summary": "JSON NOT NULL DEFAULT '{}'",
            "coverage_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "analysis_version": "INTEGER NOT NULL DEFAULT 1",
        },
        "concepts": {
            "chapter": "VARCHAR(255) NOT NULL DEFAULT ''",
            "chapter_key": "VARCHAR(160) NOT NULL DEFAULT ''",
            "granularity": "VARCHAR(32) NOT NULL DEFAULT 'concept'",
            "importance": "VARCHAR(32) NOT NULL DEFAULT 'supporting'",
        },
        "document_chunks": {
            "chunk_type": "VARCHAR(32) NOT NULL DEFAULT 'content'",
            "quality_score": "FLOAT NOT NULL DEFAULT 1.0",
            "quality_flags": "JSON NOT NULL DEFAULT '[]'",
            "usable_for_generation": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "concept_candidates": {
            "recommended_action": "VARCHAR(32) NOT NULL DEFAULT 'create'",
            "importance": "VARCHAR(32) NOT NULL DEFAULT 'supporting'",
            "confidence": "FLOAT NOT NULL DEFAULT 0.5",
            "chapter": "VARCHAR(255) NOT NULL DEFAULT ''",
            "prerequisite_names": "JSON NOT NULL DEFAULT '[]'",
            "recommendation_reason": "TEXT NOT NULL DEFAULT ''",
            "verification_status": "VARCHAR(32) NOT NULL DEFAULT 'unverified'",
            "suggested_merge_concept_id": "INTEGER",
        },
        "questions": {
            "source_type": "VARCHAR(32) NOT NULL DEFAULT 'ai_generated'",
            "question_type": "VARCHAR(32) NOT NULL DEFAULT 'short_answer'",
            "options": "JSON NOT NULL DEFAULT '[]'",
            "source_chunk_ids": "JSON NOT NULL DEFAULT '[]'",
            "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            "difficulty_level": "INTEGER NOT NULL DEFAULT 3",
            "difficulty_score": "INTEGER NOT NULL DEFAULT 50",
            "difficulty_profile": "JSON NOT NULL DEFAULT '{}'",
            "expected_minutes": "INTEGER NOT NULL DEFAULT 5",
            "difficulty_rationale": "TEXT NOT NULL DEFAULT ''",
            "moderation_status": "VARCHAR(32) NOT NULL DEFAULT 'approved'",
            "review_notes": "TEXT NOT NULL DEFAULT ''",
            "learning_objective": "TEXT NOT NULL DEFAULT ''",
            "solution_steps": "JSON NOT NULL DEFAULT '[]'",
            "source_policy": "VARCHAR(32) NOT NULL DEFAULT 'reference_guided'",
            "quality_status": "VARCHAR(32) NOT NULL DEFAULT 'passed'",
            "quality_flags": "JSON NOT NULL DEFAULT '[]'",
            "generation_provider": "VARCHAR(64) NOT NULL DEFAULT 'unknown'",
        },
        "attempts": {
            "grading_details": "JSON NOT NULL DEFAULT '{}'",
        },
        "study_tasks": {
            "progress_value": "INTEGER NOT NULL DEFAULT 0",
            "accumulated_seconds": "INTEGER NOT NULL DEFAULT 0",
            "timer_started_at": "DATETIME",
        },
        "todo_items": {
            "due_at": "DATETIME",
            "due_kind": "VARCHAR(16) NOT NULL DEFAULT 'date'",
            "priority": "VARCHAR(16) NOT NULL DEFAULT 'normal'",
            "category": "VARCHAR(32) NOT NULL DEFAULT 'personal'",
        },
        "study_sessions": {
            "parent_session_id": "INTEGER",
            "difficulty_level": "INTEGER NOT NULL DEFAULT 3",
            "concept_ids": "JSON NOT NULL DEFAULT '[]'",
            "source_policy": "VARCHAR(32) NOT NULL DEFAULT 'reference_guided'",
        },
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    connection_context = engine.begin() if isinstance(engine, Engine) else nullcontext(engine)
    with connection_context as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))
        if "todo_items" in tables:
            connection.execute(text(
                "UPDATE todo_items SET due_at = due_date || ' 23:59:00' "
                "WHERE due_at IS NULL AND due_kind = 'date'"
            ))


def drop_database() -> None:
    Base.metadata.drop_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        _session_factory.remove()
