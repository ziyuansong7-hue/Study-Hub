from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from schemas import ErrorType, Evaluation, NextAction

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "then", "to", "when", "with",
}


@dataclass(frozen=True)
class LocalGrade:
    evaluation: Evaluation
    grader: str
    definitive: bool


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _contains_phrase(answer: str, phrase: str) -> bool:
    normalized = normalize_answer(phrase)
    return bool(normalized) and f" {normalized} " in f" {answer} "


def _feedback(
    *,
    language: str,
    expected: str,
    matched: list[str],
    missing: list[str],
    score: float,
) -> str:
    if language == "zh":
        if score >= 0.85:
            return f"参考答案是：{expected}。你的表达覆盖了所需关键概念；同义术语不会被机械扣分。"
        if score > 0:
            return (
                f"参考答案是：{expected}。你已经答出：{', '.join(matched)}；"
                f"还缺少：{', '.join(missing)}。本题按关键概念给予部分分。"
            )
        return f"参考答案是：{expected}。当前回答尚未覆盖必需概念：{', '.join(missing)}。"
    if score >= 0.85:
        return (
            f"The reference answer is: {expected}. Your wording covers the required concepts; "
            "equivalent terminology is accepted."
        )
    if score > 0:
        return (
            f"The reference answer is: {expected}. You included {', '.join(matched)} but still need "
            f"{', '.join(missing)}. Partial credit was awarded for the concepts present."
        )
    return f"The reference answer is: {expected}. The response does not yet cover: {', '.join(missing)}."


def _evaluation(
    *,
    expected: str,
    confidence: float,
    language: str,
    score: float,
    matched: list[str],
    missing: list[str],
) -> Evaluation:
    correct = score >= 0.85
    low_confidence = correct and confidence < 0.45
    if correct:
        error_type = ErrorType.guessing if low_confidence else ErrorType.none
        next_action = NextAction.retry if low_confidence else NextAction.advance
        final_score = 0.8 if low_confidence else score
    elif score > 0:
        error_type = ErrorType.partial_answer
        next_action = NextAction.show_explanation
        final_score = score
    else:
        error_type = ErrorType.conceptual_misconception
        next_action = NextAction.show_explanation
        final_score = 0.0
    return Evaluation(
        is_correct=correct,
        score=final_score,
        confidence=confidence,
        error_type=error_type,
        evidence=[],
        feedback=_feedback(
            language=language,
            expected=expected,
            matched=matched,
            missing=missing,
            score=final_score,
        ),
        mastery_delta=0,
        next_action=next_action,
        conclusion_correct=correct if correct or final_score == 0 else None,
        key_steps_score=final_score,
        explanation_score=final_score,
        # Missing or non-standard wording is not automatically a fatal error.
        fatal_error=False,
    )


def grade_short_answer(question, answer: str, confidence: float, language: str) -> LocalGrade | None:
    """Return a transparent local grade when the evidence is strong enough.

    Structured ``required_concepts`` rubrics are authoritative. For legacy short factual
    questions, exact/contained answers and high lexical coverage can be accepted locally;
    ambiguous low-overlap answers are returned as ``None`` for LLM arbitration or review.
    """

    rubric = question.rubric or {}
    normalized_answer = normalize_answer(answer)
    expected = question.expected_answer.strip()
    required = rubric.get("required_concepts") or []
    if required:
        matched: list[str] = []
        missing: list[str] = []
        for item in required:
            if isinstance(item, str):
                name = item
                aliases = [item]
            else:
                name = str(item.get("name") or item.get("label") or "").strip()
                aliases = [str(value) for value in item.get("aliases", []) if str(value).strip()]
                if name and name not in aliases:
                    aliases.insert(0, name)
            if name and any(_contains_phrase(normalized_answer, alias) for alias in aliases):
                matched.append(name)
            elif name:
                missing.append(name)
        total = len(matched) + len(missing)
        score = round(len(matched) / total, 2) if total else 0.0
        return LocalGrade(
            evaluation=_evaluation(
                expected=expected,
                confidence=confidence,
                language=language,
                score=score,
                matched=matched,
                missing=missing,
            ),
            grader="local_key_concepts",
            # A full rubric match is safe to grade locally. Partial or zero lexical
            # matches may still be semantically correct wording, so let an enabled
            # external semantic grader arbitrate instead of rejecting them outright.
            definitive=score >= 0.85,
        )

    accepted_answers = [expected]
    accepted_answers.extend(
        str(value) for value in rubric.get("accepted_answers", []) if str(value).strip()
    )
    for accepted in accepted_answers:
        normalized_expected = normalize_answer(accepted)
        if normalized_answer == normalized_expected or _contains_phrase(normalized_answer, accepted):
            return LocalGrade(
                evaluation=_evaluation(
                    expected=expected,
                    confidence=confidence,
                    language=language,
                    score=1.0,
                    matched=[expected],
                    missing=[],
                ),
                grader="local_semantic",
                definitive=True,
            )

    expected_tokens = {
        token for token in normalize_answer(expected).split() if token not in STOP_WORDS and len(token) > 1
    }
    answer_tokens = set(normalized_answer.split())
    if expected_tokens:
        coverage = len(expected_tokens & answer_tokens) / len(expected_tokens)
        if coverage >= 0.8:
            return LocalGrade(
                evaluation=_evaluation(
                    expected=expected,
                    confidence=confidence,
                    language=language,
                    score=0.9,
                    matched=[expected],
                    missing=[],
                ),
                grader="local_semantic",
                definitive=True,
            )
        if coverage >= 0.4:
            return LocalGrade(
                evaluation=_evaluation(
                    expected=expected,
                    confidence=confidence,
                    language=language,
                    score=0.5,
                    matched=sorted(expected_tokens & answer_tokens),
                    missing=sorted(expected_tokens - answer_tokens),
                ),
                grader="local_semantic",
                definitive=False,
            )
    return None


def pending_review_evaluation(*, confidence: float, language: str) -> Evaluation:
    feedback = (
        "当前评分器无法可靠判断这份答案，已标记为待复核；本次不会降低掌握度。"
        if language == "zh"
        else "The current grader could not assess this answer reliably. It is pending review, and mastery was not reduced."
    )
    return Evaluation(
        is_correct=False,
        score=0,
        confidence=confidence,
        error_type=ErrorType.none,
        evidence=[],
        feedback=feedback,
        mastery_delta=0,
        next_action=NextAction.retry,
        conclusion_correct=None,
        key_steps_score=0,
        explanation_score=0,
        fatal_error=False,
    )
