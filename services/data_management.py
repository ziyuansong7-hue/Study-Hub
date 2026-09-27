from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from services.database import configure_database, init_database

REQUIRED_TABLES = {"courses", "documents", "concepts", "questions", "attempts"}


def sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("backup is only supported for the local SQLite database")
    path = Path(database_url.removeprefix("sqlite:///"))
    return path if path.is_absolute() else Path.cwd() / path


def create_backup(database_url: str) -> Path:
    source_path = sqlite_path(database_url)
    if not source_path.exists():
        raise ValueError("database file not found")
    handle, temporary = tempfile.mkstemp(prefix="studyhub-backup-", suffix=".sqlite3")
    os.close(handle)
    Path(temporary).unlink(missing_ok=True)
    Path(temporary).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(temporary)) as destination:
        source.backup(destination)
    return Path(temporary)


def restore_backup(database_url: str, uploaded_path: Path) -> Path:
    if uploaded_path.read_bytes()[:16] != b"SQLite format 3\x00":
        raise ValueError("uploaded file is not a SQLite database")
    with closing(sqlite3.connect(uploaded_path)) as candidate:
        tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED_TABLES <= tables:
        raise ValueError("uploaded database is not a compatible StudyHub backup")
    target = sqlite_path(database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    safety = target.with_name(f"{target.stem}.before-restore-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
    if target.exists():
        shutil.copy2(target, safety)
    configure_database(database_url)
    with closing(sqlite3.connect(uploaded_path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    configure_database(database_url)
    init_database()
    return safety
