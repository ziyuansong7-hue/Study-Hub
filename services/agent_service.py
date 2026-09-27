from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import date, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    AgentRun,
    Attempt,
    Concept,
    ConceptEdge,
    Document,
    DocumentChunk,
    MasteryRecord,
    StudentConceptState,
)
from repositories import LearningRepository
from schemas import AgentDecision, ErrorType, Evaluation, Evidence, NextAction
from services.answer_grading import grade_short_answer, pending_review_evaluation
from services.language_policy import language_policy
from services.llm import (
    BaseLLMProvider,
    FakeProvider,
    LLMProviderError,
    configured_provider_name,
    get_llm_provider,
)
from services.mastery import mastery_update
from services.retriever import BaseRetriever, KeywordRetriever, SearchResult

logger = logging.getLogger(__name__)

SYSTEM_POLICY = """You are StudyHub's study coach. Evaluate exactly one primary gap.
After every submitted answer, feedback must include the correct answer or conclusion, a concise step-by-step
explanation, and—when the learner is wrong—the single most important error. You may use general subject
knowledge for the explanation, but cite only supplied course evidence and never invent a source. Return only
schema-valid JSON. Use the payload's response_language for feedback: use Chinese when it is `zh` and English
when it is `en`. Preserve specialist terminology in the language used by the course evidence; do not translate
the question or evidence. Never emit private chain-of-thought; provide a concise pedagogical explanation instead."""


class AgentService:
    def __init__(
        self,
        session: Session,
        provider: BaseLLMProvider | None = None,
        retriever: BaseRetriever | None = None,
    ):
        self.session = session
        self.provider_init_error: str | None = None
        if provider is not None:
            self.provider = provider
        else:
            try:
                self.provider = get_llm_provider()
            except LLMProviderError as exc:
                self.provider = FakeProvider()
                self.provider_init_error = f"{type(exc).__name__}: {exc}"
        self.provider_explicit = provider is not None
        self.retriever = retriever or KeywordRetriever(session)
        self.repo = LearningRepository(session)

    @staticmethod
    def _provider_name(provider: BaseLLMProvider) -> str:
        name = provider.__class__.__name__.casefold()
        if "deepseek" in name:
            return "deepseek"
        if "realprovider" in name or "openai" in name:
            return "openai"
        if type(provider) is FakeProvider:
            return "local"
        return "external_model"

    @staticmethod
    def planned_grader(question) -> str:
        if question.question_type in {"single_choice", "multiple_choice", "true_false"}:
            return "programmatic"
        if question.question_type == "short_answer" and (question.rubric or {}).get("required_concepts"):
            return "local_key_concepts"
        configured = configured_provider_name()
        if question.course.external_ai_allowed and configured != "fake":
            return configured
        return "local_semantic"

    def _evaluate(
        self, payload: dict, provider: BaseLLMProvider, system_policy: str = SYSTEM_POLICY
    ) -> tuple[Evaluation, str | None]:
        last_error: str | None = None
        for _attempt in range(2):
            try:
                raw = provider.generate_json(
                    system=system_policy,
                    user=json.dumps(payload, ensure_ascii=False),
                    schema=Evaluation.model_json_schema(),
                )
                return Evaluation.model_validate(raw), None
            except (ValidationError, ValueError, TypeError, LLMProviderError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("provider output validation failed: %s", last_error)
        language = payload.get("response_language", "en")
        return pending_review_evaluation(
            confidence=float(payload.get("confidence", 0.5)), language=language
        ), last_error

    @staticmethod
    def _objective_evaluation(question, answer: str, confidence: float, language: str) -> Evaluation:
        def normalize(value: str) -> str:
            return " ".join(value.casefold().replace("，", ",").split()).strip(" .")

        expected = normalize(question.expected_answer)
        actual = normalize(answer)
        if question.question_type == "multiple_choice":
            expected_set = {normalize(value) for value in re.split(r"[,;|]", question.expected_answer) if value.strip()}
            actual_set = {normalize(value) for value in re.split(r"[,;|]", answer) if value.strip()}
            correct = expected_set == actual_set
        else:
            correct = actual == expected
        low_confidence = correct and confidence < 0.45
        if correct:
            error_type = ErrorType.guessing if low_confidence else ErrorType.none
            action = NextAction.retry if low_confidence else NextAction.advance
            score = 0.8 if low_confidence else 1.0
        else:
            error_type = ErrorType.conceptual_misconception
            action = NextAction.show_explanation
            score = 0.0
        if language == "zh":
            feedback = (
                f"正确答案是：{question.expected_answer}。本题按标准答案由程序直接判分，"
                + ("你的答案正确。" if correct else "你的答案与标准答案不一致。")
            )
        else:
            feedback = (
                f"The correct answer is: {question.expected_answer}. This objective item was graded "
                + ("correctly by direct comparison." if correct else "by direct comparison, and your answer does not match.")
            )
        return Evaluation(
            is_correct=correct,
            score=score,
            confidence=confidence,
            error_type=error_type,
            evidence=[],
            feedback=feedback,
            mastery_delta=0,
            next_action=action,
            conclusion_correct=correct,
            key_steps_score=1.0 if correct else 0.0,
            explanation_score=1.0 if correct else 0.0,
            fatal_error=not correct,
        )

    def submit_attempt(
        self,
        *,
        attempt_id: str,
        question_id: str,
        answer: str,
        confidence: float,
        student_id: str = "default",
        request_explanation: bool = False,
        response_language: str = "en",
        course_id: int | None = None,
    ) -> AgentDecision:
        response_language = "zh" if response_language == "zh" else "en"
        started = time.perf_counter()
        existing = self.repo.get_attempt(attempt_id)
        if existing:
            grading_details = existing.grading_details or {}
            saved_decision = grading_details.get("decision")
            if saved_decision:
                try:
                    return AgentDecision.model_validate(saved_decision)
                except ValidationError:
                    logger.warning("stored decision for attempt %s is invalid; using compatibility replay", attempt_id)
            replay_summary = (
                "幂等重放：返回已保存的评估。"
                if response_language == "zh"
                else "Idempotent replay: returning the saved evaluation."
            )
            return AgentDecision(
                primary_gap=existing.question.concept.name,
                rationale_summary=replay_summary,
                evaluation=Evaluation(
                    is_correct=existing.is_correct, score=existing.score, confidence=existing.confidence,
                    error_type=existing.error_type, evidence=existing.evidence, feedback=existing.feedback,
                    mastery_delta=0, next_action=existing.next_action,
                    conclusion_correct=grading_details.get("conclusion_correct"),
                    key_steps_score=grading_details.get("key_steps_score", 0.0),
                    explanation_score=grading_details.get("explanation_score", 0.0),
                    fatal_error=grading_details.get("fatal_error", False),
                ),
            )
        question = self.repo.get_question(question_id, course_id=course_id)
        if not question:
            raise ValueError(f"unknown question: {question_id}")
        if question.source_chunk_ids:
            rows = self.session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    Document.course_id == question.course_id,
                    DocumentChunk.id.in_(question.source_chunk_ids),
                )
            ).all()
            evidence_results = [
                SearchResult(
                    chunk_id=chunk.id,
                    score=1.0,
                    evidence=Evidence(
                        document=document.filename,
                        page=chunk.page,
                        section=chunk.section,
                        quote=chunk.content[:500],
                    ),
                )
                for chunk, document in rows
            ][:2]
        elif question.source_type == "ai_generated":
            evidence_results = []
        else:
            evidence_results = self.retriever.search(
                question.course_id, f"{question.evidence_query} {question.prompt}", limit=2
            )
        evidence = [item.evidence.model_dump() for item in evidence_results]
        prior_failures = self.session.scalar(select(func.count()).select_from(Attempt).where(
            Attempt.student_id == student_id,
            Attempt.question_id == question.id,
            Attempt.is_correct.is_(False),
        )) or 0
        payload = {
            "question": question.prompt,
            "expected_answer": question.expected_answer,
            "answer": answer,
            "confidence": max(0.0, min(1.0, confidence)),
            "evidence": evidence,
            "prior_failures": prior_failures,
            "response_language": response_language,
        }
        provider = self.provider
        provider_name = self._provider_name(provider)
        external_available = provider_name != "local" and not self.provider_init_error
        external_allowed = bool(question.course.external_ai_allowed or self.provider_explicit)
        grader = "pending_review"
        grade_status = "graded"
        provider_error: str | None = None
        if question.question_type in {"single_choice", "multiple_choice", "true_false"}:
            evaluation = self._objective_evaluation(
                question, answer, payload["confidence"], response_language
            )
            grader = "programmatic"
        elif question.question_type == "short_answer":
            local_grade = grade_short_answer(
                question, answer, payload["confidence"], response_language
            )
            if local_grade and not (
                self.provider_explicit and type(provider) is not FakeProvider
            ) and (
                local_grade.definitive or not external_available or not external_allowed
            ):
                evaluation = local_grade.evaluation
                grader = local_grade.grader
            elif external_available and external_allowed:
                evaluation, provider_error = self._evaluate(
                    payload,
                    provider,
                    f"{SYSTEM_POLICY}\n{language_policy(question.course, response_language)}\n"
                    "Grade four aspects explicitly: conclusion correctness, key reasoning steps, explanation sufficiency, "
                    "and whether a fatal error invalidates the solution.",
                )
                grader = provider_name
            else:
                evaluation = pending_review_evaluation(
                    confidence=payload["confidence"], language=response_language
                )
                provider_error = self.provider_init_error or (
                    "external AI is disabled for this course and the local semantic grader was inconclusive"
                    if not external_allowed
                    else "no external semantic grader is configured and the local grader was inconclusive"
                )
        else:
            if external_available and external_allowed:
                evaluation, provider_error = self._evaluate(
                    payload,
                    provider,
                    f"{SYSTEM_POLICY}\n{language_policy(question.course, response_language)}\n"
                    "Grade four aspects explicitly: conclusion correctness, key reasoning steps, explanation sufficiency, "
                    "and whether a fatal error invalidates the solution.",
                )
                grader = provider_name
            else:
                evaluation = pending_review_evaluation(
                    confidence=payload["confidence"], language=response_language
                )
                provider_error = self.provider_init_error or (
                    "external AI is disabled for this course"
                    if not external_allowed
                    else "no external semantic grader is configured"
                )

        if provider_error:
            grade_status = "pending_review"
            grader = "pending_review"

        if not evidence and not provider_error:
            evaluation.evidence = []
            if grader in {"deepseek", "openai"}:
                if response_language == "zh":
                    evaluation.feedback = (
                        "课程材料中未找到可靠依据。以下答案讲解由 AI 基于通用知识生成，"
                        f"未经课程材料验证。{evaluation.feedback}"
                    )
                else:
                    evaluation.feedback = (
                        "No reliable basis was found in the course materials. The following explanation was "
                        f"generated from general AI knowledge and was not verified against the course materials. {evaluation.feedback}"
                    )
        elif not evidence:
            evaluation.evidence = []
        else:
            # Never accept invented model citations: the provider only receives and may return retrieved evidence.
            evaluation.evidence = [item.evidence for item in evidence_results]

        prerequisite = self.session.scalar(
            select(Concept)
            .join(ConceptEdge, Concept.id == ConceptEdge.prerequisite_id)
            .where(ConceptEdge.dependent_id == question.concept_id)
            .order_by(Concept.id)
        )
        if provider_error:
            evaluation.next_action = NextAction.retry
        elif evaluation.error_type == ErrorType.missing_prerequisite and prerequisite:
            evaluation.next_action = NextAction.review_prerequisite
        elif not evaluation.is_correct:
            evaluation.next_action = NextAction.show_explanation

        previous = self.repo.latest_mastery(question.concept_id, student_id)
        if grade_status == "pending_review":
            mastery, delta = previous, 0.0
        else:
            mastery, delta = mastery_update(
                previous, score=evaluation.score, confidence=evaluation.confidence,
                is_correct=evaluation.is_correct,
            )
        evaluation.mastery_delta = delta
        primary_gap = prerequisite.name if (
            evaluation.next_action == NextAction.review_prerequisite and prerequisite
        ) else question.concept.name
        rationale_summary = (
            f"仅处理主要缺口：{primary_gap}。"
            if response_language == "zh"
            else f"Addressing the primary gap only: {primary_gap}."
        )
        decision = AgentDecision(
            primary_gap=primary_gap,
            rationale_summary=rationale_summary,
            evaluation=evaluation,
            review_concept=prerequisite.slug if prerequisite and evaluation.next_action == NextAction.review_prerequisite else None,
        )
        attempt = Attempt(
            attempt_id=attempt_id, student_id=student_id, question_id=question.id,
            answer=answer, confidence=evaluation.confidence, is_correct=evaluation.is_correct,
            score=evaluation.score, error_type=evaluation.error_type.value,
            feedback=evaluation.feedback, next_action=evaluation.next_action.value,
            evidence=[item.model_dump() for item in evaluation.evidence],
            grading_details={
                "decision": decision.model_dump(mode="json"),
                "conclusion_correct": evaluation.conclusion_correct,
                "key_steps_score": evaluation.key_steps_score,
                "explanation_score": evaluation.explanation_score,
                "fatal_error": evaluation.fatal_error,
                "grader": grader,
                "grade_status": grade_status,
                "configured_provider": configured_provider_name(),
                "course_external_ai_allowed": external_allowed,
            },
        )
        mastery_record = MasteryRecord(
            student_id=student_id, concept_id=question.concept_id,
            mastery=mastery, delta=delta, reason=f"attempt:{attempt_id}",
        )
        self.repo.save_attempt(attempt, mastery_record)
        state = self.session.scalar(select(StudentConceptState).where(
            StudentConceptState.student_id == student_id,
            StudentConceptState.concept_id == question.concept_id,
        ))
        if not state:
            state = StudentConceptState(
                student_id=student_id,
                concept_id=question.concept_id,
                mastery=previous,
                correct_streak=0,
                failure_streak=0,
                average_confidence=0.0,
                attempts_count=0,
                last_error_type="none",
                needs_review=False,
            )
            self.session.add(state)
        previous_count = state.attempts_count or 0
        state.attempts_count = previous_count + 1
        state.average_confidence = (
            ((state.average_confidence or 0.0) * previous_count + evaluation.confidence)
            / state.attempts_count
        )
        state.mastery = mastery
        state.last_practiced_at = attempt.created_at
        if grade_status != "pending_review":
            state.correct_streak = (state.correct_streak or 0) + 1 if evaluation.is_correct else 0
            state.failure_streak = 0 if evaluation.is_correct else (state.failure_streak or 0) + 1
            state.last_error_type = evaluation.error_type.value
            state.needs_review = (
                mastery < 0.55
                or evaluation.next_action in {NextAction.review_prerequisite, NextAction.schedule_review}
            )
        if evaluation.next_action == NextAction.schedule_review:
            self.repo.schedule_review(
                question.concept_id, date.today() + timedelta(days=1), "agent decision", student_id
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.session.add(AgentRun(
            run_id=str(uuid.uuid4()), student_id=student_id, operation="study_attempt",
            input_summary={"attempt_id": attempt_id, "question_id": question_id, "answer_length": len(answer)},
            tools_used=[
                "search_course_material",
                f"grade:{grader}",
                "save_attempt",
            ],
            decision=decision.model_dump(mode="json"), duration_ms=duration_ms,
            status="degraded" if provider_error else "success", error=provider_error,
        ))
        self.session.flush()
        return decision
