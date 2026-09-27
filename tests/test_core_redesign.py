from __future__ import annotations

from sqlalchemy import select

from app import create_app
from models import Concept, Course, Document, DocumentChunk, Question, StudySession
from services.content_quality import question_quality
from services.database import session_scope
from services.material_service import _outline_rows
from services.retriever import KeywordRetriever


def test_textbook_outline_ignores_contents_and_uses_body_pages():
    rows = _outline_rows([
        (4, None, "CONTENTS\nCHAPTER 1 What Is Economics? 1\nCHAPTER 2 The Economic Problem 33\nCHAPTER 3 Demand and Supply 59"),
        (26, None, "2 CHAPTER 1 What Is Economics?\nScarcity requires choices."),
        (58, None, "34 CHAPTER 2 The Economic Problem\nProduction possibilities describe tradeoffs."),
    ], "textbook")
    assert [(row[1], row[4]) for row in rows] == [
        ("Chapter 1: What Is Economics?", 26),
        ("Chapter 2: The Economic Problem", 58),
    ]


def test_question_quality_quarantines_extraction_fragments():
    status, flags = question_quality(
        "Which option best describes Capital and Natural Resource Markets?",
        "re block, VMP,",
        options=["re block, VMP,", "Not Capital and Natural Resource Markets", "None of the above"],
    )
    assert status == "quarantined"
    assert "incomplete_answer_fragment" in flags
    assert "placeholder_choice_set" in flags


def test_retriever_bounds_large_corpus_and_finds_distinctive_match(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        document = Document(
            course_id=course.id,
            filename="large-reference.txt",
            content_hash="bounded-retrieval",
            status="processed",
        )
        session.add(document)
        session.flush()
        for index in range(450):
            session.add(DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                page=index + 1,
                content=(
                    "Ordinary material about study examples."
                    if index < 449
                    else "Amortized analysis explains aggregate sequence cost."
                ),
            ))
        session.flush()
        results = KeywordRetriever(session).search(
            course.id, "amortized aggregate analysis", limit=3
        )
        assert results
        assert results[0].evidence.page == 450


def test_retriever_supports_chinese_material(db_url):
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        document = Document(
            course_id=course.id,
            filename="chinese-notes.md",
            content_hash="chinese-retrieval",
            status="processed",
        )
        session.add(document)
        session.flush()
        session.add(DocumentChunk(
            document_id=document.id,
            chunk_index=0,
            page=1,
            content="需求价格弹性衡量需求量对价格变化的反应程度。",
        ))
        session.flush()
        results = KeywordRetriever(session).search(course.id, "需求价格弹性", limit=2)
        assert results and results[0].evidence.document == "chinese-notes.md"


def test_empty_course_can_start_reference_guided_session_on_demand(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({"TESTING": True, "DATABASE_URL": db_url})
    client = app.test_client()
    with session_scope() as session:
        course = Course(code="ECON", title="Economics", external_ai_allowed=False)
        session.add(course)
        session.flush()
        concept = Concept(
            course_id=course.id,
            slug="elasticity",
            name="Price Elasticity of Demand",
            description="Responsiveness of quantity demanded to a price change.",
            importance="core",
        )
        session.add(concept)
        session.flush()
        course_id, concept_id = course.id, concept.id

    response = client.post("/study/start", data={
        "course_id": str(course_id),
        "mode": "focus",
        "concept_ids": str(concept_id),
        "difficulty_level": "3",
        "target_count": "2",
        "question_type": "short_answer",
        "source_policy": "reference_guided",
    })
    assert response.status_code == 302
    assert "session_id=" in response.location
    with session_scope() as session:
        created = session.scalar(
            select(StudySession).where(StudySession.course_id == course_id)
        )
        assert created and len(created.items) == 2
        assert all(item.question.quality_status == "passed" for item in created.items)
        assert all(item.question.source_policy == "reference_guided" for item in created.items)
        assert session.scalar(select(Question).where(Question.course_id == course_id))
