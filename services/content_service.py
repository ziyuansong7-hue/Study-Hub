from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    AgentRun,
    Concept,
    ConceptCandidate,
    ConceptEdge,
    ConceptSource,
    Course,
    Document,
    DocumentChunk,
    Question,
)
from schemas import ConceptExtraction, ConceptReviewBatch, DifficultyReviewBatch, QuestionGeneration
from services.content_quality import question_quality
from services.difficulty import difficulty_level, difficulty_score, level_target_score
from services.language_policy import language_policy
from services.llm import BaseLLMProvider, FakeProvider, LLMProviderError, get_llm_provider
from services.retriever import KeywordRetriever

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:90] or f"concept-{uuid.uuid4().hex[:8]}"


def _clean_concept_name(value: str) -> str:
    name = " ".join(value.replace("\ufffd\ufffd", "'").replace("\ufffd", "'").split()).strip(" -–—:;,.•")
    name = re.sub(r"^\d+\s+(?=CHAPTER\b)", "", name, flags=re.I)
    name = re.sub(r"^CHAPTER\s+\d+\s+", "", name, flags=re.I)
    name = re.sub(r"\s+\d{1,4}$", "", name).strip(" -–—:;,.•")
    if name.casefold() in {"summary", "additional problems and applications", "study plan"}:
        return ""
    if re.search(r"\b(and|or|of|the|to|for)$", name, flags=re.I):
        return ""
    return name


class ConceptDiscoveryService:
    MAX_EXTRACTION_CHUNKS = 80
    MAX_CANDIDATES = 40

    def __init__(self, session: Session, provider: BaseLLMProvider | None = None):
        self.session = session
        self.provider = provider or get_llm_provider()
        self.provider_explicit = provider is not None

    def _provider_for(self, course: Course) -> BaseLLMProvider:
        if not self.provider_explicit and not isinstance(self.provider, FakeProvider) and not course.external_ai_allowed:
            return FakeProvider()
        return self.provider

    @staticmethod
    def _provider_label(provider: BaseLLMProvider) -> str:
        return "local_heuristic" if isinstance(provider, FakeProvider) else provider.__class__.__name__.replace("Provider", "").casefold()

    @classmethod
    def _representative_chunks(
        cls, chunks: list[DocumentChunk], limit: int | None = None
    ) -> list[DocumentChunk]:
        """Bound model cost while sampling the beginning and the full document."""
        limit = min(limit or cls.MAX_EXTRACTION_CHUNKS, cls.MAX_EXTRACTION_CHUNKS)
        if len(chunks) <= limit:
            return chunks
        selected: dict[int, DocumentChunk] = {}

        # Front matter often contains the table of contents and is highly useful
        # for a textbook-level concept map.
        for chunk in chunks[:min(16, limit)]:
            selected[chunk.id] = chunk

        section_chunks = [
            chunk for chunk in chunks
            if chunk.section
            and ".indd" not in chunk.section.casefold()
            and not re.fullmatch(r"[\W\d_]+", chunk.section)
        ]
        remaining = limit - len(selected)
        pool = section_chunks or chunks
        if remaining > 0:
            step = max(1, len(pool) / remaining)
            for index in range(remaining):
                selected[pool[min(int(index * step), len(pool) - 1)].id] = pool[min(int(index * step), len(pool) - 1)]

        # Fill any collisions with evenly distributed general chunks.
        if len(selected) < limit:
            step = max(1, len(chunks) / (limit - len(selected)))
            index = 0.0
            while len(selected) < limit and int(index) < len(chunks):
                chunk = chunks[int(index)]
                selected[chunk.id] = chunk
                index += step
        return sorted(selected.values(), key=lambda chunk: chunk.chunk_index)[:limit]

    def extract(self, document_id: int) -> int:
        started = time.perf_counter()
        document = self.session.get(Document, document_id)
        if not document or document.status != "processed":
            raise ValueError("processed document not found")
        chunks = list(self.session.scalars(select(DocumentChunk).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.usable_for_generation.is_(True),
        ).order_by(DocumentChunk.chunk_index)))
        if not chunks:
            raise ValueError("document has no readable chunks")
        total_chunk_count = len(chunks)
        chunks = self._representative_chunks(
            chunks,
            40
            if document.analysis_mode == "quick" and document.material_type != "auto"
            else self.MAX_EXTRACTION_CHUNKS,
        )
        course = document.course
        provider = self._provider_for(course)
        document.analysis_status = "extracting_concepts"
        document.analysis_provider = self._provider_label(provider)
        extracted = []
        error = None
        for offset in range(0, len(chunks), 20):
            batch = chunks[offset: offset + 20]
            payload = {
                "course_id": document.course_id,
                "document": document.filename,
                "chunks": [
                    {"id": chunk.id, "section": chunk.section, "page": chunk.page, "content": chunk.content[:1600]}
                    for chunk in batch
                ],
            }
            extraction = None
            for _ in range(2):
                try:
                    raw = provider.generate_json(
                        system=(
                            "Extract a concise list of teachable concepts from the supplied course material. "
                            "Ignore repeated headers, footers, course codes, page numbers, and administrative text. "
                            "For each candidate recommend create, merge, rename, ignore, or review; classify it as core, "
                            "supporting, or optional; provide confidence, chapter, prerequisites, and a short grounded reason. "
                            "Merge near-duplicate names within this batch, use only supplied chunks, and attach their ids. "
                            f"{language_policy(course)} Return schema-valid JSON only."
                        ),
                        user=json.dumps(payload, ensure_ascii=False),
                        schema=ConceptExtraction.model_json_schema(),
                    )
                    extraction = ConceptExtraction.model_validate(raw)
                    break
                except (ValidationError, ValueError, TypeError, LLMProviderError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    logger.warning("concept extraction failed: %s", error)
            if extraction:
                extracted.extend(extraction.concepts)
        if not extracted:
            suggestions = []
            for chunk in chunks:
                name = _clean_concept_name(chunk.section or "")
                if name and name.casefold() not in {item[0].casefold() for item in suggestions}:
                    suggestions.append((name, [chunk.id], chunk.content[:240]))
            if not suggestions:
                suggestions = [(f"Material topic {index + 1}", [chunk.id], chunk.content[:240]) for index, chunk in enumerate(chunks[:8])]
        else:
            valid_ids = {chunk.id for chunk in chunks}
            merged: dict[str, tuple[str, set[int], str, object]] = {}
            for item in extracted:
                clean_name = _clean_concept_name(item.name)
                normalized = re.sub(r"[^\w]+", "", clean_name.casefold())
                if not normalized:
                    continue
                ids = {chunk_id for chunk_id in item.source_chunk_ids if chunk_id in valid_ids}
                if normalized in merged:
                    original_name, previous_ids, description, previous_item = merged[normalized]
                    merged[normalized] = (original_name, previous_ids | ids, description or item.description.strip(), previous_item)
                else:
                    merged[normalized] = (clean_name, ids, item.description.strip(), item)
            suggestions = [(name, sorted(ids), description, item) for name, ids, description, item in merged.values()]
        if extracted:
            structured_suggestions = suggestions
        else:
            structured_suggestions = [(name, ids, description, None) for name, ids, description in suggestions]
        structured_suggestions = structured_suggestions[:self.MAX_CANDIDATES]
        created = 0
        for name, chunk_ids, description, item in structured_suggestions:
            if not name:
                continue
            existing = self.session.scalar(select(ConceptCandidate).where(
                ConceptCandidate.document_id == document.id,
                ConceptCandidate.name == name[:255],
            ))
            if existing:
                continue
            self.session.add(ConceptCandidate(
                document_id=document.id,
                name=name[:255],
                description=description[:500],
                source_chunk_ids=chunk_ids,
                recommended_action=item.recommended_action if item else "review",
                importance=item.importance if item else "supporting",
                confidence=item.confidence if item else 0.4,
                chapter=(item.chapter if item else "")[:255],
                prerequisite_names=item.prerequisites if item else [],
                recommendation_reason=(item.reason if item else "Extracted from a material heading; review required.")[:1000],
                verification_status="local_heuristic" if isinstance(provider, FakeProvider) else "ai_suggested",
            ))
            created += 1
        self.session.flush()
        total_candidates = self.session.scalar(
            select(func.count()).select_from(ConceptCandidate).where(
                ConceptCandidate.document_id == document.id
            )
        ) or 0
        candidate_rows = list(self.session.scalars(
            select(ConceptCandidate).where(ConceptCandidate.document_id == document.id)
        ))
        summary = dict(document.analysis_summary or {})
        summary.update({
            "candidate_count": total_candidates,
            "core_candidate_count": sum(value.importance == "core" for value in candidate_rows),
            "supporting_candidate_count": sum(value.importance == "supporting" for value in candidate_rows),
            "optional_candidate_count": sum(value.importance == "optional" for value in candidate_rows),
            "recommendation_counts": {
                action: sum(value.recommended_action == action for value in candidate_rows)
                for action in ("create", "merge", "rename", "ignore", "review")
            },
            "sampled_chunk_count": len(chunks),
            "total_chunk_count": total_chunk_count,
        })
        document.analysis_summary = summary
        document.analysis_status = "awaiting_review"
        self.session.add(AgentRun(
            run_id=str(uuid.uuid4()),
            operation="extract_concepts",
            input_summary={
                "document_id": document.id,
                "total_chunk_count": total_chunk_count,
                "sampled_chunk_count": len(chunks),
            },
            tools_used=["read_document_chunks"],
            decision={"candidates_created": created},
            duration_ms=int((time.perf_counter() - started) * 1000),
            status="degraded" if error else "success",
            error=error,
        ))
        self.session.flush()
        return created

    def verify(self, candidate_ids: list[int]) -> int:
        candidate_ids = list(dict.fromkeys(int(value) for value in candidate_ids))[:100]
        candidates = list(self.session.scalars(
            select(ConceptCandidate).where(ConceptCandidate.id.in_(candidate_ids))
        ))
        if not candidates:
            raise ValueError("no concept candidates selected")
        course_ids = {candidate.document.course_id for candidate in candidates}
        if len(course_ids) != 1:
            raise ValueError("selected candidates must belong to one course")
        course = candidates[0].document.course
        provider = self._provider_for(course)
        existing = list(self.session.scalars(
            select(Concept).where(Concept.course_id == course.id).order_by(Concept.name)
        ))
        updated = 0
        for offset in range(0, len(candidates), 20):
            batch = candidates[offset:offset + 20]
            chunk_ids = {
                chunk_id for candidate in batch for chunk_id in (candidate.source_chunk_ids or [])
            }
            chunks = {
                chunk.id: chunk for chunk in self.session.scalars(
                    select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids or {-1}))
                )
            }
            payload = {
                "course": course.title,
                "existing_concepts": [concept.name for concept in existing],
                "candidates": [
                    {
                        "candidate_id": candidate.id,
                        "name": candidate.name,
                        "description": candidate.description,
                        "evidence": [
                            {"id": cid, "page": chunks[cid].page, "section": chunks[cid].section,
                             "content": chunks[cid].content[:900]}
                            for cid in candidate.source_chunk_ids if cid in chunks
                        ],
                    }
                    for candidate in batch
                ],
            }
            result = None
            for _attempt in range(2):
                try:
                    raw = provider.generate_json(
                        system=(
                            "Independently verify concept candidates against only their course evidence. Recommend create, "
                            "merge, rename, ignore, or review; classify importance; provide confidence, chapter, prerequisites, "
                            "a concise reason, and an existing merge target when appropriate. Never claim whole-course coverage. "
                            f"{language_policy(course)} Return schema-valid JSON only."
                        ),
                        user=json.dumps(payload, ensure_ascii=False),
                        schema=ConceptReviewBatch.model_json_schema(),
                    )
                    result = ConceptReviewBatch.model_validate(raw)
                    break
                except (ValidationError, ValueError, TypeError, LLMProviderError) as exc:
                    logger.warning("concept verification failed: %s", exc)
            if result is None:
                continue
            candidate_map = {candidate.id: candidate for candidate in batch}
            existing_by_name = {concept.name.casefold(): concept for concept in existing}
            for review in result.reviews:
                candidate = candidate_map.get(review.candidate_id)
                if not candidate:
                    continue
                candidate.recommended_action = review.recommended_action
                candidate.importance = review.importance
                candidate.confidence = review.confidence
                candidate.chapter = review.chapter[:255]
                candidate.prerequisite_names = review.prerequisites
                candidate.recommendation_reason = review.reason
                candidate.suggested_merge_concept_id = (
                    existing_by_name.get((review.merge_target or "").casefold()).id
                    if (review.merge_target or "").casefold() in existing_by_name else None
                )
                candidate.verification_status = (
                    "local_heuristic" if isinstance(provider, FakeProvider) else "ai_verified"
                )
                updated += 1
        for document in {candidate.document for candidate in candidates}:
            document.analysis_provider = self._provider_label(provider)
            document.analysis_status = "awaiting_review"
        self.session.flush()
        return updated

    def decide(
        self,
        candidate_id: int,
        *,
        action: str,
        name: str = "",
        merge_concept_id: int | None = None,
    ) -> ConceptCandidate:
        candidate = self.session.get(ConceptCandidate, candidate_id)
        if not candidate:
            raise ValueError("concept candidate not found")
        if action == "reject":
            candidate.status = "rejected"
            self.session.flush()
            return candidate
        if action == "confirm" and candidate.status == "confirmed":
            return candidate
        if action != "confirm":
            raise ValueError("unknown candidate action")
        document = candidate.document
        if merge_concept_id:
            concept = self.session.get(Concept, merge_concept_id)
            if not concept or concept.course_id != document.course_id:
                raise ValueError("merge target is outside this course")
        else:
            clean_name = name.strip() or candidate.name
            base_slug = _slugify(clean_name)
            slug = base_slug
            suffix = 2
            while self.session.scalar(select(Concept).where(
                Concept.course_id == document.course_id, Concept.slug == slug
            )):
                slug = f"{base_slug[:85]}-{suffix}"
                suffix += 1
            concept = Concept(
                course_id=document.course_id,
                slug=slug,
                name=clean_name[:255],
                description=candidate.description,
                chapter=candidate.chapter,
                chapter_key=_slugify(candidate.chapter) if candidate.chapter else "",
                importance=candidate.importance,
            )
            self.session.add(concept)
            self.session.flush()
        valid_chunk_ids = set(self.session.scalars(select(DocumentChunk.id).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.id.in_(candidate.source_chunk_ids or [-1]),
        )))
        for chunk_id in valid_chunk_ids:
            if not self.session.scalar(select(ConceptSource).where(
                ConceptSource.concept_id == concept.id, ConceptSource.chunk_id == chunk_id
            )):
                self.session.add(ConceptSource(concept_id=concept.id, chunk_id=chunk_id))
        for prerequisite_name in candidate.prerequisite_names or []:
            prerequisite = self.session.scalar(select(Concept).where(
                Concept.course_id == document.course_id,
                func.lower(Concept.name) == str(prerequisite_name).strip().casefold(),
            ))
            if prerequisite and prerequisite.id != concept.id and not self.session.scalar(
                select(ConceptEdge).where(
                    ConceptEdge.prerequisite_id == prerequisite.id,
                    ConceptEdge.dependent_id == concept.id,
                )
            ):
                self.session.add(ConceptEdge(
                    prerequisite_id=prerequisite.id,
                    dependent_id=concept.id,
                ))
        candidate.status = "confirmed"
        candidate.confirmed_concept_id = concept.id
        self.session.flush()
        return candidate


class QuestionGenerationService:
    def __init__(self, session: Session, provider: BaseLLMProvider | None = None):
        self.session = session
        self.provider = provider or get_llm_provider()
        self.provider_explicit = provider is not None

    def _provider_for(self, course: Course) -> BaseLLMProvider:
        if not self.provider_explicit and not isinstance(self.provider, FakeProvider) and not course.external_ai_allowed:
            return FakeProvider()
        return self.provider

    @staticmethod
    def _provider_label(provider: BaseLLMProvider) -> str:
        return (
            "fake"
            if isinstance(provider, FakeProvider)
            else provider.__class__.__name__.replace("Provider", "").casefold()
        )

    def generate(
        self,
        *,
        course_id: int,
        concept_id: int,
        count: int,
        difficulty: float,
        question_type: str,
        material_only: bool,
        source_policy: str = "reference_guided",
        target_level: int | None = None,
        review_feedback: str = "",
    ) -> list[Question]:
        started = time.perf_counter()
        concept = self.session.get(Concept, concept_id)
        if not concept or concept.course_id != course_id:
            raise ValueError("concept does not belong to the selected course")
        course = concept.course
        provider = self._provider_for(course)
        if source_policy not in {"reference_guided", "material_strict", "exam_style"}:
            raise ValueError("invalid question source policy")
        if material_only:
            source_policy = "material_strict"
        target_level = max(1, min(5, target_level or round(max(0.0, min(1.0, difficulty)) * 4) + 1))
        links = list(self.session.scalars(select(ConceptSource).where(
            ConceptSource.concept_id == concept.id
        )))
        chunks = [self.session.get(DocumentChunk, link.chunk_id) for link in links]
        chunks = [chunk for chunk in chunks if chunk and chunk.usable_for_generation]
        if not chunks:
            result_ids = [item.chunk_id for item in KeywordRetriever(self.session).search(
                course_id, f"{concept.name} {concept.description}", limit=5
            )]
            chunks = list(self.session.scalars(select(DocumentChunk).where(
                DocumentChunk.id.in_(result_ids or [-1]),
                DocumentChunk.usable_for_generation.is_(True),
            )))
        if source_policy == "material_strict" and not chunks:
            raise ValueError("not enough confirmed course material to generate a reliable question")
        calibration_chunks = []
        if target_level >= 4:
            calibration_chunks = list(self.session.scalars(
                select(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    Document.course_id == course_id,
                    Document.status == "processed",
                    Document.source_category == "past_exam",
                )
                .order_by(Document.source_year.desc(), DocumentChunk.id)
                .limit(6)
            ))
        payload = {
            "concept": concept.name,
            "concept_description": concept.description,
            "count": max(1, min(count, 10)),
            "difficulty": max(0.0, min(1.0, difficulty)),
            "target_level": target_level,
            "target_score": level_target_score(target_level),
            "question_type": question_type,
            "material_only": material_only,
            "source_policy": source_policy,
            "chunks": [
                {
                    "id": chunk.id,
                    "section": chunk.section,
                    "page": chunk.page,
                    "source_category": chunk.document.source_category,
                    "content": chunk.content[:1800],
                }
                for chunk in chunks
            ],
            "calibration_examples": [
                {
                    "year": chunk.document.source_year,
                    "page": chunk.page,
                    "content": chunk.content[:1200],
                }
                for chunk in calibration_chunks
            ],
            "review_feedback": review_feedback,
        }
        result = None
        error = None
        for _ in range(2):
            try:
                raw = provider.generate_json(
                    system=(
                        "Create unambiguous practice questions for exactly one concept and the requested difficulty level. "
                        "Difficulty must come from reasoning depth, integration, novelty, and workload—not obscure wording. "
                        "Level 4 represents a demanding university exam question; Level 5 is an advanced university exam "
                        "challenge and must not be described as official or institution-certified. If user-uploaded past-exam "
                        "examples are supplied, calibrate reasoning depth and workload to them without copying their wording. "
                        "In reference_guided mode, use chunks only to establish scope and terminology, then write an original "
                        "question and self-contained answer from reliable general knowledge. Never copy raw excerpts as answers. "
                        "In material_strict mode, every answer claim must be supported by supplied chunks and include their ids. "
                        "In exam_style mode, imitate only reasoning depth and workload, never wording. Do not invent citations. "
                        f"{language_policy(course)} Return schema-valid JSON only."
                    ),
                    user=json.dumps(payload, ensure_ascii=False),
                    schema=QuestionGeneration.model_json_schema(),
                )
                result = QuestionGeneration.model_validate(raw)
                break
            except (ValidationError, ValueError, TypeError, LLMProviderError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("question generation failed: %s", error)
        if result is None:
            raise ValueError("question generation failed; no questions were saved")
        review_payload = {
            "target_level": target_level,
            "course": course.title,
            "concept": concept.name,
            "questions": [item.model_dump(mode="json") for item in result.questions],
        }
        assessment_by_index = {}
        try:
            raw_review = provider.generate_json(
                system=(
                    "Independently review each generated question. Recalculate the six difficulty dimensions, detect ambiguity, "
                    "check course scope, estimate time, and assign a 0-100 score plus Level 1-5. Return schema-valid JSON only."
                ),
                user=json.dumps(review_payload, ensure_ascii=False),
                schema=DifficultyReviewBatch.model_json_schema(),
            )
            reviewed = DifficultyReviewBatch.model_validate(raw_review)
            assessment_by_index = {item.index: item for item in reviewed.assessments}
        except (ValidationError, ValueError, TypeError, LLMProviderError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("difficulty review failed: %s", error)
        flagged = [
            assessment for assessment in assessment_by_index.values()
            if abs(assessment.difficulty_level - target_level) > 1
            or assessment.ambiguity_detected
            or not assessment.within_course_scope
        ]
        if flagged and not review_feedback:
            feedback = "; ".join(
                f"question {item.index}: estimated Level {item.difficulty_level}; {item.rationale}"
                for item in flagged
            )
            return self.generate(
                course_id=course_id,
                concept_id=concept_id,
                count=count,
                difficulty=difficulty,
                question_type=question_type,
                material_only=material_only,
                source_policy=source_policy,
                target_level=target_level,
                review_feedback=f"Rewrite once to address independent review: {feedback}",
            )
        valid_chunk_ids = {chunk.id for chunk in chunks}
        created: list[Question] = []
        for index, item in enumerate(result.questions[:count]):
            source_ids = [value for value in item.source_chunk_ids if value in valid_chunk_ids]
            if source_policy == "material_strict" and not source_ids:
                continue
            source_type = "material_based" if source_policy == "material_strict" else "ai_generated"
            quality_status, quality_flags = question_quality(
                item.prompt, item.expected_answer, options=item.options
            )
            digest = hashlib.sha256(
                f"{course_id}|{concept_id}|{item.prompt}".encode("utf-8")
            ).hexdigest()[:20]
            external_id = f"generated-{digest}"
            question = self.session.scalar(select(Question).where(
                Question.course_id == course_id, Question.external_id == external_id
            ))
            if question:
                continue
            assessment = assessment_by_index.get(index)
            profile = (
                assessment.difficulty_profile.model_dump()
                if assessment else item.difficulty_profile.model_dump()
            )
            score = assessment.difficulty_score if assessment else difficulty_score(profile)
            level = assessment.difficulty_level if assessment else difficulty_level(score)
            rationale = assessment.rationale if assessment else "Calculated from the generated difficulty dimensions."
            review_notes = ""
            if assessment and (assessment.ambiguity_detected or not assessment.within_course_scope):
                review_notes = "Reviewer flagged ambiguity or material outside the selected course scope."
            if abs(level - target_level) > 1:
                review_notes = f"Requested Level {target_level}; independent review estimated Level {level}."
            question = Question(
                course_id=course_id,
                concept_id=concept_id,
                external_id=external_id,
                prompt=item.prompt,
                expected_answer=item.expected_answer,
                rubric={"generated": True, "points": item.rubric_points},
                learning_objective=item.learning_objective or f"Apply {concept.name}",
                solution_steps=item.solution_steps,
                difficulty=score / 100,
                difficulty_level=level,
                difficulty_score=score,
                difficulty_profile=profile,
                expected_minutes=assessment.expected_minutes if assessment else item.expected_minutes,
                difficulty_rationale=rationale,
                evidence_query=item.evidence_query or concept.name,
                source_type=source_type,
                source_policy=source_policy,
                quality_status=quality_status,
                quality_flags=quality_flags,
                generation_provider=self._provider_label(provider),
                question_type=item.question_type,
                options=item.options,
                source_chunk_ids=source_ids,
                is_active=False,
                moderation_status="rejected" if quality_status == "quarantined" else "pending",
                review_notes=(
                    f"Automatically quarantined: {', '.join(quality_flags)}"
                    if quality_flags else review_notes
                ),
            )
            self.session.add(question)
            self.session.flush()
            created.append(question)
        if not created:
            raise ValueError("course material did not support a reliable generated question")
        self.session.add(AgentRun(
            run_id=str(uuid.uuid4()),
            operation="generate_questions",
            input_summary={
                "course_id": course_id,
                "concept_id": concept_id,
                "count": count,
                "material_only": material_only,
            },
            tools_used=["read_concept_sources"],
            decision={"question_ids": [question.external_id for question in created]},
            duration_ms=int((time.perf_counter() - started) * 1000),
            status="degraded" if error else "success",
            error=error,
        ))
        self.session.flush()
        return created


class QuestionModerationService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, question_id: int) -> Question:
        question = self.session.get(Question, question_id)
        if not question:
            raise ValueError("question not found")
        return question

    def action(self, question_id: int, action: str) -> Question:
        question = self.get(question_id)
        if action == "approve":
            if question.quality_status == "quarantined":
                raise ValueError("quarantined questions must be edited and pass quality checks before approval")
            question.moderation_status = "approved"
            question.is_active = True
        elif action == "reject":
            question.moderation_status = "rejected"
            question.is_active = False
        elif action == "disable":
            question.is_active = False
        elif action == "enable":
            if question.moderation_status != "approved" or question.quality_status != "passed":
                raise ValueError("only approved questions that passed quality checks can be enabled")
            question.is_active = True
        else:
            raise ValueError("unknown question action")
        self.session.flush()
        return question

    def edit(self, question_id: int, **values) -> Question:
        question = self.get(question_id)
        prompt = str(values.get("prompt", question.prompt)).strip()
        answer = str(values.get("expected_answer", question.expected_answer)).strip()
        if not prompt or not answer:
            raise ValueError("question and expected answer are required")
        question.prompt = prompt[:4000]
        question.expected_answer = answer[:4000]
        question.question_type = str(values.get("question_type", question.question_type))
        if "options" in values:
            raw_options = str(values.get("options", ""))
            options = [line.strip() for line in raw_options.splitlines() if line.strip()]
            if question.question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
                raise ValueError("choice questions require at least two options")
            question.options = options
        question.expected_minutes = max(1, min(90, int(values.get("expected_minutes", question.expected_minutes))))
        question.review_notes = str(values.get("review_notes", question.review_notes)).strip()[:2000]
        quality_status, quality_flags = question_quality(
            question.prompt, question.expected_answer, options=question.options
        )
        question.quality_status = quality_status
        question.quality_flags = quality_flags
        question.moderation_status = "rejected" if quality_status == "quarantined" else "pending"
        question.is_active = False
        self.session.flush()
        return question
