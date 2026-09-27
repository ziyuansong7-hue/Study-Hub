from __future__ import annotations

import io
from datetime import date, timedelta

from sqlalchemy import func, select

from app import create_app
from models import ReviewTask
from services.agent_service import AgentService
from services.database import configure_database, session_scope
from services.llm import FakeProvider
from services.scheduler import create_daily_reviews


def test_all_pages_and_api_flow(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    for path in [
        "/", "/courses", "/materials", "/questions", "/study",
        "/mistakes", "/tasks", "/agent-runs", "/data",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
    response = client.post("/api/attempts", json={
        "attempt_id": "web-a1", "question_id": "demo-q7",
        "answer": "exactly one", "confidence": 0.9,
    })
    assert response.status_code == 200
    assert response.json["decision"]["evaluation"]["is_correct"] is True
    assert client.get("/agent-runs").data.find(b"save_attempt") >= 0


def test_language_switch_translates_ui_and_coach_feedback(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    assert b"Study Session" in client.get("/study").data

    response = client.post("/language/zh", data={"next": "/study"}, follow_redirects=True)
    assert "学习练习".encode() in response.data
    assert 'name="concept_ids"'.encode() in response.data
    assert "开始答题".encode() not in response.data

    response = client.post("/api/attempts", json={
        "attempt_id": "zh-web-a1", "question_id": "demo-q7",
        "answer": "exactly one", "confidence": 0.9,
    })
    assert response.status_code == 200
    assert "参考答案是" in response.json["decision"]["evaluation"]["feedback"]

    response = client.post("/language/en", data={"next": "/study"}, follow_redirects=True)
    assert b"Study Session" in response.data


def test_material_upload_is_idempotent(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    payload = b"# Custom Section\n[page 1]\nA unique user supplied fact."
    for _ in range(2):
        response = client.post(
            "/materials", data={"material": (io.BytesIO(payload), "notes.md")},
            content_type="multipart/form-data", follow_redirects=True,
        )
        assert response.status_code == 200


def test_upload_limit_is_large_book_friendly_and_has_clear_error(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    assert app.config["MAX_CONTENT_LENGTH"] == 128 * 1024 * 1024

    limited_app = create_app({
        "TESTING": True,
        "DATABASE_URL": db_url,
        "MAX_CONTENT_LENGTH": 1024,
    })
    response = limited_app.test_client().post(
        "/materials",
        data={"course_id": "1", "material": (io.BytesIO(b"x" * 2048), "large.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert b"STUDYHUB_MAX_UPLOAD_MB" in response.data


def test_daily_review_job_does_not_duplicate(db_url):
    configure_database(db_url)
    with session_scope() as session:
        AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="weak", question_id="demo-q1", answer="wrong", confidence=0.9
        )
    due = date.today() + timedelta(days=1)
    first = create_daily_reviews(db_url, due_date=due)
    second = create_daily_reviews(db_url, due_date=due)
    assert first["created"] == 1
    assert second["created"] == 0
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(ReviewTask)) == 1
