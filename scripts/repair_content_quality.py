from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from models import (
    Concept,
    ConceptSource,
    Course,
    Document,
    DocumentChunk,
    DocumentSection,
    Question,
)
from services.content_quality import question_quality
from services.database import configure_database, init_database, session_scope
from services.material_service import MaterialService


def _chapter_for_page(sections: list[DocumentSection], page: int | None) -> DocumentSection | None:
    if not page:
        return None
    eligible = [row for row in sections if row.start_page and row.start_page <= page]
    return max(eligible, key=lambda row: (row.start_page or 0, row.position)) if eligible else None


def repair(database_url: str) -> dict[str, int]:
    configure_database(database_url)
    init_database()
    stats = {
        "documents_refreshed": 0,
        "questions_quarantined": 0,
        "questions_passed": 0,
        "concept_chapters_updated": 0,
        "course_titles_repaired": 0,
    }
    with session_scope() as session:
        for course in session.scalars(select(Course)):
            if course.code.upper() == "ECON101" and ("\ufffd" in course.title or len(course.title.strip()) < 4):
                course.title = "Microeconomics"
                stats["course_titles_repaired"] += 1

        documents = list(session.scalars(
            select(Document).where(Document.status == "processed").order_by(Document.id)
        ))
        material_service = MaterialService(session)
        for document in documents:
            material_service.refresh_metadata(document.id)
            stats["documents_refreshed"] += 1

        for question in session.scalars(select(Question).order_by(Question.id)):
            status, flags = question_quality(
                question.prompt,
                question.expected_answer,
                options=question.options or [],
            )
            question.quality_status = status
            question.quality_flags = flags
            if status == "quarantined":
                question.is_active = False
                question.moderation_status = "rejected"
                question.review_notes = "Automatically quarantined: " + ", ".join(flags)
                stats["questions_quarantined"] += 1
            else:
                # Preserve explicit human review state. Only approved questions
                # are active; clean drafts remain visible in the review inbox.
                question.is_active = question.moderation_status == "approved"
                stats["questions_passed"] += 1

        for concept in session.scalars(select(Concept).order_by(Concept.id)):
            sources = list(session.execute(
                select(DocumentChunk, Document)
                .join(ConceptSource, ConceptSource.chunk_id == DocumentChunk.id)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    ConceptSource.concept_id == concept.id,
                    DocumentChunk.usable_for_generation.is_(True),
                )
                .order_by(DocumentChunk.page, DocumentChunk.chunk_index)
            ))
            if not sources:
                continue
            chunk, document = sources[0]
            sections = list(session.scalars(
                select(DocumentSection)
                .where(DocumentSection.document_id == document.id)
                .order_by(DocumentSection.position)
            ))
            section = _chapter_for_page(sections, chunk.page)
            if section and concept.chapter != section.title:
                concept.chapter = section.title
                concept.chapter_key = section.section_key
                stats["concept_chapters_updated"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reassess imported chunks, rebuild material structure, and quarantine broken questions."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db"),
    )
    args = parser.parse_args()
    stats = repair(args.database_url)
    print("Content-quality repair complete")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
