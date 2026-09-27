from __future__ import annotations

import hashlib
import io
import logging
import math
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import ConceptCandidate, Course, Document, DocumentChunk, DocumentSection
from services.content_quality import assess_chunk, normalize_extracted_text

logger = logging.getLogger(__name__)


class UnsupportedMaterialError(ValueError):
    pass


def _extract_text(filename: str, content: bytes) -> tuple[str, list[int]]:
    suffix = Path(filename).suffix.casefold()
    if suffix in {".txt", ".md"}:
        try:
            return content.decode("utf-8"), []
        except UnicodeDecodeError as exc:
            raise UnsupportedMaterialError("text materials must use UTF-8 encoding") from exc
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            # Some publisher/iOS PDFs have recoverable broken xref entries. pypdf's
            # non-strict mode can repair them, but otherwise emits thousands of
            # warnings that obscure the useful import result.
            pypdf_logger = logging.getLogger("pypdf")
            old_level = pypdf_logger.level
            pypdf_logger.setLevel(logging.ERROR)
            try:
                reader = PdfReader(io.BytesIO(content), strict=False)
                if reader.is_encrypted:
                    try:
                        reader.decrypt("")
                    except Exception as exc:
                        raise UnsupportedMaterialError("This PDF is password-protected and cannot be read.") from exc
                extracted_pages: list[str] = []
                failed_pages: list[int] = []
                for page_number, page in enumerate(reader.pages, start=1):
                    try:
                        extracted_pages.append((page.extract_text() or "").strip())
                    except Exception:
                        # A damaged page must not make a 500-page readable textbook fail.
                        extracted_pages.append("")
                        failed_pages.append(page_number)
                if failed_pages:
                    logger.warning(
                        "PDF import skipped %s unreadable pages; first pages=%s",
                        len(failed_pages), failed_pages[:10],
                    )
            finally:
                pypdf_logger.setLevel(old_level)
            if sum(len(value) for value in extracted_pages) < max(50, len(extracted_pages) * 10):
                raise UnsupportedMaterialError(
                    "This PDF contains too little extractable text and may be scanned. OCR is not available in this MVP."
                )
            edge_lines: Counter[str] = Counter()
            page_lines: list[list[str]] = []
            for value in extracted_pages:
                lines = [line.strip() for line in value.splitlines() if line.strip()]
                page_lines.append(lines)
                for line in lines[:2] + lines[-2:]:
                    if len(line) <= 160:
                        edge_lines[line] += 1
            threshold = max(3, math.ceil(len(page_lines) * 0.3))
            repeated = {line for line, count in edge_lines.items() if count >= threshold}
            pages = []
            for number, lines in enumerate(page_lines, start=1):
                cleaned = "\n".join(line for line in lines if line not in repeated)
                pages.append(f"[page {number}]\n{cleaned}")
            return "\n\n".join(pages), failed_pages
        except UnsupportedMaterialError:
            raise
        except Exception as exc:
            raise UnsupportedMaterialError(f"unable to read PDF: {exc}") from exc
    raise UnsupportedMaterialError("supported material types are .txt, .md, and .pdf")


def _chunk_text(text: str, max_chars: int = 1200) -> list[tuple[int | None, str | None, str]]:
    page: int | None = None
    section: str | None = None
    buffer: list[str] = []
    chunks: list[tuple[int | None, str | None, str]] = []

    def flush() -> None:
        nonlocal buffer
        joined = "\n".join(buffer).strip()
        while joined:
            chunks.append((page, section, joined[:max_chars]))
            joined = joined[max_chars:].strip()
        buffer = []

    for line in text.splitlines():
        stripped = line.strip()
        page_match = re.match(r"\s*\[page\s+(\d+)\]\s*", line, re.I)
        heading_match = re.match(r"\s*#{1,6}\s+(.+)", line)
        if page_match:
            flush()
            page = int(page_match.group(1))
            section = None
        elif heading_match:
            flush()
            section = heading_match.group(1).strip()
        elif (
            not buffer
            and stripped
            and 2 <= len(stripped.split()) <= 14
            and len(stripped) <= 120
            and not stripped.endswith((".", ";", ":", "?", "!"))
        ):
            # PDF slide titles rarely carry Markdown markers. Keep a conservative
            # first-line heuristic so source locators are more useful than "No section".
            section = stripped
            buffer.append(stripped)
        elif sum(len(item) for item in buffer) + len(line) > max_chars:
            flush()
            buffer.append(line)
        else:
            buffer.append(line)
    flush()
    return [chunk for chunk in chunks if chunk[2]]


MATERIAL_TYPES = {
    "auto", "textbook", "syllabus", "unit_slides", "lecture", "tutorial",
    "assignment", "past_exam", "supplementary_reading",
}


def _detect_material_type(
    filename: str, text: str, source_category: str, page_count: int
) -> str:
    searchable = f"{filename}\n{text[:50000]}".casefold()
    filename_lower = filename.casefold()
    if source_category == "past_exam":
        return "past_exam"
    if source_category == "assignment":
        return "assignment"
    if source_category == "tutorial":
        return "tutorial"
    if re.search(r"\b(course outline|syllabus|grading scheme)\b", searchable):
        return "syllabus"
    chapter_count = len(set(re.findall(r"(?im)^\s*chapter\s+(\d+)\b", text)))
    if page_count >= 120 or chapter_count >= 5:
        return "textbook"
    if re.search(r"\b(midterm|final exam|past exam)\b", filename_lower):
        return "past_exam"
    if re.search(r"\b(assignment|problem set|homework)\b", filename_lower):
        return "assignment"
    if "tutorial" in filename_lower:
        return "tutorial"
    if re.search(r"\b(unit|module)\s+\d+\b", searchable):
        return "unit_slides"
    if Path(filename).suffix.casefold() == ".pdf" and page_count <= 100:
        return "lecture"
    return "supplementary_reading"


def _outline_rows(
    chunks: list[tuple[int | None, str | None, str]], material_type: str
) -> list[tuple[str, str, str, int, int | None]]:
    rows: dict[str, tuple[str, str, str, int, int | None]] = {}
    position = 0
    toc_pages = {
        page
        for page, section, content in chunks
        if page is not None and (
            "detailed contents" in content.casefold()
            or assess_chunk(content, section).chunk_type == "table_of_contents"
        )
    }
    for page, section, content in chunks:
        quality = assess_chunk(content, section)
        # A table of contents may contain perfectly formatted chapter titles,
        # but those page numbers are printed-book references rather than PDF
        # source locations. Never use it to establish chapter boundaries.
        if quality.chunk_type == "table_of_contents" or page in toc_pages:
            continue
        header_text = f"{section or ''}\n{quality.content}"
        has_chapter_header = bool(re.search(
            r"(?im)^\s*(?:\d{1,4}\s+)?CHAPTER\s+\d+\s+[^\n]{2,100}$", header_text
        ))
        if material_type == "textbook" and not quality.usable and not has_chapter_header:
            continue
        chapter_matches = re.findall(
            r"(?im)^\s*(?:\d{1,4}\s+)?CHAPTER\s+(\d+)\s+([^\n]{2,100})$", header_text
        )
        for number, raw_title in chapter_matches:
            first_alpha = next((character for character in raw_title if character.isalpha()), "")
            if first_alpha and not first_alpha.isupper():
                # Avoid prose such as “Chapter 1 as an example …”. Running
                # headers and real chapter titles start with title case.
                continue
            title = re.sub(r"\s+\d{1,4}\s*$", "", raw_title).strip(" .")
            title = re.sub(r"\s{2,}", " ", title)
            if not title:
                continue
            key = f"chapter-{number}"
            if key not in rows:
                rows[key] = (key, f"Chapter {number}: {title}", "chapter", position, page)
                position += 1
        if material_type != "textbook" and section and quality.usable:
            title = " ".join(section.split()).strip()
            if not title or ".indd" in title.casefold() or len(title) > 180:
                continue
            normalized = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
            key = f"section-{normalized[:120]}"
            if normalized and key not in rows:
                rows[key] = (key, title, "section", position, page)
                position += 1
        if len(rows) >= 200:
            break
    return list(rows.values())


class MaterialService:
    def __init__(self, session: Session):
        self.session = session

    def _process(self, document: Document) -> None:
        assert document.original_content is not None
        text, failed_pages = _extract_text(document.filename, document.original_content)
        text = normalize_extracted_text(text)
        chunks = _chunk_text(text)
        if not chunks:
            raise UnsupportedMaterialError("material contains no extractable text")
        page_count = max((page or 0 for page, _section, _content in chunks), default=0)
        detected = _detect_material_type(
            document.filename, text, document.source_category, page_count
        )
        document.detected_material_type = detected
        if document.material_type_source == "auto" or document.material_type == "auto":
            document.material_type = detected
        if document.analysis_mode == "auto":
            document.analysis_mode = (
                "structured" if document.material_type in {"textbook", "syllabus", "unit_slides"}
                else "quick"
            )
        quality_counts: Counter[str] = Counter()
        for index, (page, section, chunk) in enumerate(chunks):
            quality = assess_chunk(chunk, section)
            quality_counts[quality.chunk_type] += 1
            self.session.add(DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                content=quality.content,
                page=page,
                section=section,
                chunk_type=quality.chunk_type,
                quality_score=quality.score,
                quality_flags=quality.flags,
                usable_for_generation=quality.usable,
            ))
        outline = _outline_rows(chunks, document.material_type)
        for key, title, level, position, start_page in outline:
            self.session.add(DocumentSection(
                document_id=document.id,
                section_key=key,
                title=title,
                level=level,
                position=position,
                start_page=start_page,
            ))
        document.analysis_summary = {
            "page_count": page_count,
            "chunk_count": len(chunks),
            "section_count": len(outline),
            "material_type": document.material_type,
            "analysis_mode": document.analysis_mode,
            "unreadable_page_count": len(failed_pages),
            "unreadable_pages": failed_pages[:50],
            "quality_counts": dict(quality_counts),
            "usable_chunk_count": sum(
                1 for page, section, chunk in chunks if assess_chunk(chunk, section).usable
            ),
            "scope_claim": "uploaded_material_only",
        }
        document.analysis_status = "ready"
        document.status = "processed"

    def refresh_metadata(self, document_id: int) -> Document:
        document = self.session.get(Document, document_id)
        if not document or document.status != "processed":
            raise ValueError("processed document not found")
        chunk_rows = list(self.session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        ))
        if not chunk_rows:
            raise ValueError("document has no readable chunks")
        tuples = [(chunk.page, chunk.section, chunk.content) for chunk in chunk_rows]
        quality_counts: Counter[str] = Counter()
        usable_count = 0
        for chunk in chunk_rows:
            quality = assess_chunk(chunk.content, chunk.section)
            chunk.content = quality.content
            chunk.chunk_type = quality.chunk_type
            chunk.quality_score = quality.score
            chunk.quality_flags = quality.flags
            chunk.usable_for_generation = quality.usable
            quality_counts[quality.chunk_type] += 1
            usable_count += int(quality.usable)
        tuples = [(chunk.page, chunk.section, chunk.content) for chunk in chunk_rows]
        page_count = max((chunk.page or 0 for chunk in chunk_rows), default=0)
        sample_text = "\n".join(chunk.content for chunk in chunk_rows[:120])
        detected = _detect_material_type(
            document.filename, sample_text, document.source_category, page_count
        )
        document.detected_material_type = detected
        if document.material_type_source == "auto" or document.material_type in {"", "auto", "unknown"}:
            document.material_type = detected
        if document.analysis_mode in {"", "auto"}:
            document.analysis_mode = (
                "structured" if document.material_type in {"textbook", "syllabus", "unit_slides"}
                else "quick"
            )
        self.session.execute(
            delete(DocumentSection).where(DocumentSection.document_id == document.id)
        )
        self.session.flush()
        outline = _outline_rows(tuples, document.material_type)
        for key, title, level, position, start_page in outline:
            self.session.add(DocumentSection(
                document_id=document.id, section_key=key, title=title, level=level,
                position=position, start_page=start_page,
            ))
        summary = dict(document.analysis_summary or {})
        summary.update({
            "page_count": page_count,
            "chunk_count": len(chunk_rows),
            "section_count": len(outline),
            "material_type": document.material_type,
            "analysis_mode": document.analysis_mode,
            "unreadable_page_count": summary.get("unreadable_page_count", 0),
            "quality_counts": dict(quality_counts),
            "usable_chunk_count": usable_count,
            "scope_claim": "uploaded_material_only",
        })
        document.analysis_summary = summary
        if document.analysis_status in {"", "not_started"}:
            document.analysis_status = "ready"
        self.session.flush()
        return document

    def import_bytes(
        self,
        course_identifier: int | str,
        filename: str,
        content: bytes,
        *,
        source_category: str = "course_material",
        source_year: int | None = None,
        material_type: str = "auto",
        analysis_mode: str = "auto",
        coverage_enabled: bool = False,
    ) -> tuple[Document, bool]:
        course = (
            self.session.get(Course, course_identifier)
            if isinstance(course_identifier, int)
            else self.session.scalar(select(Course).where(Course.code == course_identifier))
        )
        if not course:
            raise ValueError(f"unknown course: {course_identifier}")
        if material_type not in MATERIAL_TYPES:
            raise ValueError("invalid material type")
        if analysis_mode not in {"auto", "quick", "structured"}:
            raise ValueError("invalid analysis mode")
        digest = hashlib.sha256(content).hexdigest()
        existing = self.session.scalar(select(Document).where(
            Document.course_id == course.id, Document.content_hash == digest
        ))
        if existing:
            return existing, False
        document = Document(
            course_id=course.id,
            filename=Path(filename).name,
            content_hash=digest,
            status="processing",
            source_category=source_category,
            source_year=source_year,
            original_content=content,
            material_type=material_type,
            material_type_source="auto" if material_type == "auto" else "user",
            analysis_mode=analysis_mode,
            coverage_enabled=bool(coverage_enabled),
        )
        self.session.add(document)
        self.session.flush()
        try:
            self._process(document)
        except Exception as exc:
            document.status = "failed"
            document.error_message = str(exc)[:1000]
        self.session.flush()
        return document, True

    def reprocess(self, document_id: int) -> Document:
        document = self.session.get(Document, document_id)
        if not document:
            raise ValueError("document not found")
        if not document.original_content:
            raise ValueError("the original file was not retained; upload it again to reprocess")
        for candidate in list(self.session.scalars(select(ConceptCandidate).where(
            ConceptCandidate.document_id == document.id
        ))):
            self.session.delete(candidate)
        for chunk in list(self.session.scalars(select(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        ))):
            self.session.delete(chunk)
        for section in list(self.session.scalars(select(DocumentSection).where(
            DocumentSection.document_id == document.id
        ))):
            self.session.delete(section)
        self.session.flush()
        document.status = "processing"
        document.error_message = None
        try:
            self._process(document)
        except Exception as exc:
            document.status = "failed"
            document.error_message = str(exc)[:1000]
        self.session.flush()
        return document
