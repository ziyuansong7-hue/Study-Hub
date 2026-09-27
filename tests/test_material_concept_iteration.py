from __future__ import annotations

import io

from sqlalchemy import func, select

from app import create_app
from models import (
    Concept,
    ConceptCandidate,
    Course,
    Document,
    PracticeScopePreset,
    Question,
    StudySession,
)
from services.database import session_scope
from services.material_service import MaterialService


def _upload(client, course_id: int) -> tuple[int, bytes]:
    payload = (
        b"# Unit 4 Elasticity\n"
        b"Price elasticity of demand measures responsiveness to a price change.\n"
        b"# Income Elasticity\nIncome elasticity measures responsiveness to income."
    )
    response = client.post(
        "/materials",
        data={
            "course_id": str(course_id),
            "material_type": "unit_slides",
            "analysis_mode": "structured",
            "coverage_enabled": "on",
            "material": (io.BytesIO(payload), "unit-4.md"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with session_scope() as session:
        document = session.scalar(select(Document).where(Document.filename == "unit-4.md"))
        assert document
        return document.id, payload


def test_material_page_hides_chunks_and_metadata_is_type_aware(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    with session_scope() as session:
        course_id = session.scalar(select(Course.id).order_by(Course.id))
    document_id, _payload = _upload(client, course_id)

    response = client.get(f"/materials?course_id={course_id}&document_id={document_id}")
    assert response.status_code == 200
    assert b"Price elasticity of demand measures" not in response.data
    assert b"unit-4.md" in response.data
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document.material_type == "unit_slides"
        assert document.analysis_mode == "structured"
        assert document.analysis_summary["scope_claim"] == "uploaded_material_only"
        MaterialService(session).refresh_metadata(document_id)
        MaterialService(session).refresh_metadata(document_id)

    chunk_page = client.get(f"/materials/{document_id}/chunks")
    assert chunk_page.status_code == 200
    assert b"Price elasticity of demand measures" in chunk_page.data
    assert b"<details" in chunk_page.data


def test_bulk_concept_review_is_idempotent(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    with session_scope() as session:
        course_id = session.scalar(select(Course.id).order_by(Course.id))
    document_id, _payload = _upload(client, course_id)
    assert client.post(f"/materials/{document_id}/extract-concepts").status_code == 302

    with session_scope() as session:
        ids = list(session.scalars(select(ConceptCandidate.id).where(
            ConceptCandidate.document_id == document_id
        )))
    assert len(ids) >= 2
    verified = client.post("/concepts/bulk", json={"action": "verify", "candidate_ids": ids})
    assert verified.status_code == 200
    assert verified.json["changed"] == len(ids)

    first = client.post("/concepts/bulk", json={"action": "confirm", "candidate_ids": ids})
    second = client.post("/concepts/bulk", json={"action": "confirm", "candidate_ids": ids})
    assert first.status_code == second.status_code == 200
    with session_scope() as session:
        candidates = list(session.scalars(select(ConceptCandidate).where(
            ConceptCandidate.id.in_(ids)
        )))
        assert all(value.status == "confirmed" for value in candidates)
        names = {value.name for value in candidates}
        concept_count = session.scalar(select(func.count()).select_from(Concept).where(
            Concept.course_id == course_id, Concept.name.in_(names)
        ))
        assert concept_count == len(names)


def test_practice_allows_multi_concept_but_focus_rejects_it(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    with session_scope() as session:
        questions = list(session.scalars(select(Question).where(
            Question.is_active.is_(True), Question.moderation_status == "approved"
        ).order_by(Question.id).limit(2)))
        assert len(questions) == 2
        course_id = questions[0].course_id
        concept_ids = list(dict.fromkeys(value.concept_id for value in questions))
        if len(concept_ids) < 2:
            more = session.scalar(select(Concept).where(
                Concept.course_id == course_id, Concept.id != concept_ids[0]
            ))
            assert more
            clone = Question(
                external_id="multi-scope-extra",
                course_id=course_id,
                concept_id=more.id,
                prompt="Explain the selected concept.",
                expected_answer="A grounded explanation.",
                question_type="short_answer",
                source_type="demo",
                moderation_status="approved",
                is_active=True,
            )
            session.add(clone)
            session.flush()
            concept_ids.append(more.id)

    form = {
        "course_id": str(course_id),
        "mode": "practice",
        "concept_ids": [str(value) for value in concept_ids[:2]],
        "difficulty": "0.5",
        "difficulty_level": "3",
        "target_count": "2",
        "question_type": "any",
        "preset_name": "Midterm 1",
    }
    response = client.post("/study/start", data=form)
    assert response.status_code == 302 and "session_id=" in response.location
    assert "/study/session/" in response.location
    with session_scope() as session:
        created = session.scalar(select(StudySession).order_by(StudySession.id.desc()))
        assert set(created.concept_ids) == set(concept_ids[:2])
        assert all(item.question.concept_id in concept_ids[:2] for item in created.items)
        assert {item.question.concept_id for item in created.items} == set(concept_ids[:2])
        preset = session.scalar(select(PracticeScopePreset).where(
            PracticeScopePreset.course_id == course_id,
            PracticeScopePreset.name == "Midterm 1",
        ))
        assert preset and set(preset.concept_ids) == set(concept_ids[:2])
        preset_id = preset.id

    assert client.post(f"/study/presets/{preset_id}/delete").status_code == 302
    with session_scope() as session:
        assert session.get(PracticeScopePreset, preset_id) is None

    form["mode"] = "focus"
    form["preset_name"] = ""
    rejected = client.post("/study/start", data=form)
    assert rejected.status_code == 302
    assert rejected.location.endswith("/study")
