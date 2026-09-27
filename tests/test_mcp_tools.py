from __future__ import annotations

from datetime import date

from mcp_server import tools
from models import Course
from services.database import session_scope


def _use_db(monkeypatch, db_url):
    monkeypatch.setenv("STUDYHUB_DATABASE_URL", db_url)
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")


def test_search_course_material_and_structured_error(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    result = tools.search_course_material("De Morgan")
    assert result["ok"] and result["results"]
    error = tools.search_course_material("")
    assert error == {"ok": False, "error": {"code": "invalid_request", "message": "query must not be empty"}}


def test_search_filters_common_word_only_matches(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    result = tools.search_course_material("function domain outputs input")
    assert result["ok"] and len(result["results"]) == 1
    assert result["results"][0]["section"] == "Relations and Functions"


def test_get_student_state_and_not_found(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    assert tools.get_student_state()["ok"] is True
    assert tools.get_student_state(course_code="NOPE")["error"]["code"] == "not_found"


def test_mcp_requires_course_when_multiple_courses_exist(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    with session_scope() as session:
        session.add(Course(code="MATH999", title="Another Course"))
    ambiguous = tools.get_student_state()
    assert ambiguous["error"]["code"] == "invalid_request"
    explicit = tools.get_student_state(course_code="CS245")
    assert explicit["ok"] is True
    assert explicit["course"]["code"] == "CS245"


def test_save_attempt_and_validation_error(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    result = tools.save_attempt("mcp-a1", "demo-q7", "exactly one", 0.9)
    assert result["ok"] is True
    assert result["decision"]["evaluation"]["is_correct"] is True
    assert tools.save_attempt("bad", "demo-q7", "x", 2)["error"]["code"] == "invalid_request"


def test_schedule_review_is_idempotent_and_validates(db_url, monkeypatch):
    _use_db(monkeypatch, db_url)
    due = date.today().isoformat()
    first = tools.schedule_review("sets", due, "weak mastery")
    second = tools.schedule_review("sets", due, "weak mastery")
    assert first["ok"] and first["created"] is True
    assert second["ok"] and second["created"] is False
    assert tools.schedule_review("sets", "tomorrow", "x")["error"]["code"] == "invalid_request"
