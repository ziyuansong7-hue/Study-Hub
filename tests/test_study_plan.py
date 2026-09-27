from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app import create_app
from models import Concept, Course, StudyTask
from services.agent_service import AgentService
from services.database import session_scope
from services.llm import FakeProvider
from services.study_plan_service import StudyPlanService


def _scope(session):
    course = session.scalar(select(Course).where(Course.code == "CS245"))
    concept = session.scalar(select(Concept).where(Concept.slug == "sets"))
    assert course and concept
    return course, concept


def test_manual_task_full_lifecycle_and_progress(db_url):
    with session_scope() as session:
        course, concept = _scope(session)
        service = StudyPlanService(session)
        task = service.create_manual(
            course_id=course.id, concept_id=concept.id, title="Practice sets",
            mode="focus", target_type="questions", target_value=5,
            due_date=date.today(), notes="Finish before lunch",
        )
        assert service.dashboard()["total"] == 1
        assert service.question_for_task(task.id).external_id == "demo-q5"

        service.edit(
            task.id, course_id=course.id, concept_id=concept.id,
            title="Practice set operations", mode="review", target_type="minutes",
            target_value=20, due_date=date.today(), notes="Updated",
        )
        assert task.title == "Practice set operations"
        assert task.target_type == "minutes" and task.target_value == 20

        service.action(task.id, "complete")
        plan = service.dashboard()
        assert plan["completed"] == 1 and plan["percent"] == 100

        service.action(task.id, "reopen")
        assert service.dashboard()["completed"] == 0
        service.action(task.id, "postpone")
        assert task.due_date == date.today() + timedelta(days=1)
        assert service.dashboard()["total"] == 0

        service.action(task.id, "delete")
        assert task.status == "dismissed"


def test_ai_recommendation_requires_acceptance(db_url):
    with session_scope() as session:
        AgentService(session, FakeProvider()).submit_attempt(
            attempt_id="plan-weak", question_id="demo-q1", answer="wrong", confidence=0.8
        )
        service = StudyPlanService(session)
        assert service.ensure_ai_recommendations() == 1
        assert service.ensure_ai_recommendations() == 0
        plan = service.dashboard()
        assert plan["total"] == 0
        assert len(plan["recommendations"]) == 1
        recommendation = plan["recommendations"][0]
        assert recommendation.source == "ai_recommendation"

        service.action(recommendation.id, "accept")
        plan = service.dashboard()
        assert len(plan["recommendations"]) == 0
        assert plan["total"] == 1


def test_study_plan_web_actions(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    with session_scope() as session:
        course, concept = _scope(session)
        course_id, concept_id = course.id, concept.id

    response = client.post("/study-plan/create", data={
        "title": "My set practice", "course_id": course_id, "concept_id": concept_id,
        "mode": "focus", "target_type": "questions", "target_value": 5,
        "due_date": date.today().isoformat(), "notes": "Manual task",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"My set practice" in response.data

    with session_scope() as session:
        task = session.scalar(select(StudyTask).where(StudyTask.title == "My set practice"))
        assert task
        task_id = task.id

    response = client.get(f"/study-plan/{task_id}/start")
    assert response.status_code == 302
    assert "question_id=demo-q5" in response.location

    response = client.post(f"/study-plan/{task_id}/complete", follow_redirects=True)
    assert b"1 / 1 completed" in response.data
    response = client.post(f"/study-plan/{task_id}/delete", follow_redirects=True)
    assert b"My set practice" not in response.data


def test_todo_create_returns_to_tasks_and_rejects_external_redirect(db_url):
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()

    page = client.get("/tasks?view=inbox")
    assert b'name="next" value="/tasks?view=inbox"' in page.data

    response = client.post(
        "/todos/create",
        data={"title": "Stay in task center", "next": "/tasks?view=inbox"},
    )
    assert response.status_code == 302
    assert response.location == "/tasks?view=inbox"

    for unsafe_target in ("https://example.com", "//example.com", "/\\example.com"):
        response = client.post(
            "/todos/create",
            data={"title": "Reject external redirect", "next": unsafe_target},
        )
        assert response.status_code == 302
        assert response.location == "/planner"
