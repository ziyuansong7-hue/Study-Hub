from __future__ import annotations

import io
from datetime import date, timedelta

from sqlalchemy import select

from app import create_app
from models import (
    Concept,
    ConceptCandidate,
    Course,
    Document,
    Question,
    SessionItem,
    StudySession,
    TodoItem,
)
from services.database import session_scope


def _create_course(client) -> int:
    response = client.post("/courses/create", data={
        "title": "Algorithms",
        "code": "",
        "material_language": "en",
        "explanation_language": "zh",
        "terminology_policy": "english_terms",
        "color": "#ff9000",
        "description": "Personal algorithms workspace",
    }, follow_redirects=True)
    assert response.status_code == 200
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.title == "Algorithms"))
        assert course and course.code_is_generated is True
        assert course.display_code == ""
        return course.id


def test_multi_course_material_concept_and_session_flow(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    course_id = _create_course(client)

    payload = b"# Asymptotic Analysis\nBig O describes an asymptotic upper bound on growth."
    response = client.post("/materials", data={
        "course_id": str(course_id),
        "material": (io.BytesIO(payload), "algorithms.md"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    with session_scope() as session:
        document = session.scalar(select(Document).where(Document.course_id == course_id))
        assert document and document.filename == "algorithms.md"
        document_id = document.id

    response = client.post(f"/materials/{document_id}/extract-concepts", follow_redirects=True)
    assert response.status_code == 200
    with session_scope() as session:
        candidate = session.scalar(select(ConceptCandidate).where(
            ConceptCandidate.document_id == document_id,
            ConceptCandidate.status == "pending",
        ))
        assert candidate
        candidate_id = candidate.id

    response = client.post(
        f"/concept-candidates/{candidate_id}/confirm",
        data={"name": "Asymptotic Analysis", "merge_concept_id": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with session_scope() as session:
        concept = session.scalar(select(Concept).where(
            Concept.course_id == course_id, Concept.name == "Asymptotic Analysis"
        ))
        assert concept
        concept_id = concept.id

    response = client.post("/questions/generate", data={
        "course_id": course_id,
        "concept_id": concept_id,
        "count": 2,
        "difficulty_level": 4,
        "question_type": "short_answer",
        "material_only": "on",
    }, follow_redirects=True)
    assert response.status_code == 200
    with session_scope() as session:
        drafts = list(session.scalars(select(Question).where(
            Question.course_id == course_id,
            Question.moderation_status == "pending",
        )))
        assert len(drafts) == 2
        assert all(question.difficulty_level == 4 for question in drafts)
        draft_ids = [question.id for question in drafts]
    for draft_id in draft_ids:
        client.post(f"/questions/{draft_id}/approve")

    response = client.post("/study/start", data={
        "course_id": course_id,
        "mode": "focus",
        "concept_id": concept_id,
        "difficulty": "0.5",
        "difficulty_level": "4",
        "target_count": "2",
        "question_type": "short_answer",
        "material_only": "on",
    })
    assert response.status_code == 302
    assert "session_id=" in response.location
    response = client.get(response.location)
    assert response.status_code == 200
    assert b"Algorithms" in response.data

    with session_scope() as session:
        study_session = session.scalar(select(StudySession).where(StudySession.course_id == course_id))
        assert study_session and study_session.target_count == 2
        assert len(study_session.items) == 2
        assert all(item.question.source_type == "material_based" for item in study_session.items)
        item = study_session.items[0]
        form = {
            "session_id": study_session.session_key,
            "session_item_id": item.id,
            "question_id": item.question.external_id,
            "attempt_id": "multi-course-attempt",
            "answer": item.question.expected_answer,
            "confidence": "0.9",
        }
        session_key = study_session.session_key
    response = client.post("/study", data=form)
    assert response.status_code == 200
    assert b"multi-course-attempt" not in response.data
    with session_scope() as session:
        item = session.scalar(select(SessionItem).where(SessionItem.attempt_id == "multi-course-attempt"))
        assert item and item.status == "answered"
        study_session = session.scalar(select(StudySession).where(StudySession.session_key == session_key))
        assert study_session and study_session.status == "active"


def test_personal_todo_is_independent_from_courses(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    response = client.post("/todos/create", data={
        "title": "Prepare interview",
        "notes": "Review project stories",
        "due_date": date.today().isoformat(),
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Prepare interview" in response.data
    with session_scope() as session:
        item = session.scalar(select(TodoItem).where(TodoItem.title == "Prepare interview"))
        assert item
        item_id = item.id

    client.post(f"/todos/{item_id}/postpone")
    with session_scope() as session:
        assert session.get(TodoItem, item_id).due_date == date.today() + timedelta(days=1)
    client.post(f"/todos/{item_id}/complete")
    with session_scope() as session:
        assert session.get(TodoItem, item_id).status == "completed"
    client.post(f"/todos/{item_id}/delete")
    with session_scope() as session:
        assert session.get(TodoItem, item_id).status == "dismissed"


def test_courses_page_and_health_use_studyhub(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    assert client.get("/courses").status_code == 200
    assert client.get("/api/health").json == {"ok": True, "service": "studyhub"}
