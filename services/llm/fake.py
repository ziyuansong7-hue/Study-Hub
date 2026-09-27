from __future__ import annotations

import json
import re
from typing import Any

from services.difficulty import difficulty_level, difficulty_score
from services.llm.base import BaseLLMProvider


class FakeProvider(BaseLLMProvider):
    """Deterministic provider for demos and tests; no network or key required."""

    def generate_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(user)
        properties = schema.get("properties", {})
        if "concepts" in properties:
            concepts = []
            seen: set[str] = set()
            for chunk in payload.get("chunks", []):
                section = str(chunk.get("section") or "").strip()
                content = str(chunk.get("content", ""))
                chapter_names = [
                    re.sub(r"\s+\d{1,4}\s*$", "", match).strip(" .")
                    for match in re.findall(r"(?im)^\s*CHAPTER\s+\d+\s+(.+)$", content)
                ]
                names = chapter_names or ([re.sub(r"\s+\d{1,4}\s*$", "", section).strip()] if section else [])
                for name in names:
                    if not name or name.casefold() in seen:
                        continue
                    seen.add(name.casefold())
                    concepts.append({
                        "name": name,
                        "description": content[:240],
                        "source_chunk_ids": [int(chunk["id"])],
                        "recommended_action": "create",
                        "importance": "core" if chapter_names else "supporting",
                        "confidence": 0.72 if chapter_names or section else 0.45,
                        "chapter": section if section.upper().startswith("CHAPTER") else "",
                        "prerequisites": [],
                        "reason": "Grounded in an imported chapter or section heading.",
                        "merge_target": None,
                    })
            return {"concepts": concepts[:12] or [{
                "name": "Imported material",
                "description": "A topic extracted from the imported material.",
                "source_chunk_ids": [],
                "recommended_action": "review",
                "importance": "supporting",
                "confidence": 0.3,
                "chapter": "",
                "prerequisites": [],
                "reason": "No reliable heading was found; manual review is required.",
                "merge_target": None,
            }]}
        if "reviews" in properties:
            existing = {str(value).casefold(): str(value) for value in payload.get("existing_concepts", [])}
            reviews = []
            for candidate in payload.get("candidates", []):
                name = str(candidate.get("name", "")).strip()
                evidence = candidate.get("evidence", [])
                merge_target = existing.get(name.casefold())
                reviews.append({
                    "candidate_id": int(candidate["candidate_id"]),
                    "recommended_action": "merge" if merge_target else ("create" if evidence else "review"),
                    "importance": "core" if name.casefold() not in {"summary", "introduction"} else "optional",
                    "confidence": 0.8 if evidence else 0.35,
                    "chapter": str(evidence[0].get("section") or "") if evidence else "",
                    "prerequisites": [],
                    "reason": "The recommendation is grounded in imported material evidence." if evidence else "No linked evidence was available; manual review is required.",
                    "merge_target": merge_target,
                })
            return {"reviews": reviews}
        if "questions" in properties:
            requested_type = str(payload.get("question_type", "short_answer"))
            question_type = requested_type if requested_type != "any" else "short_answer"
            chunks = payload.get("chunks", [])
            concept = str(payload.get("concept", "the selected concept"))
            count = max(1, min(int(payload.get("count", 1)), 10))
            level = max(1, min(5, int(payload.get("target_level", 3))))
            profiles = {
                1: [1, 1, 0, 0, 0, 0],
                2: [2, 2, 1, 1, 1, 1],
                3: [3, 3, 2, 2, 2, 2],
                4: [3, 3, 3, 3, 2, 3],
                5: [4, 4, 4, 4, 3, 4],
            }
            values = profiles[level]
            profile = dict(zip(
                ["conceptual_depth", "reasoning_steps", "topic_integration", "novelty", "calculation_load", "prerequisite_breadth"],
                values,
                strict=True,
            ))
            questions = []
            safe_definitions = {
                "elasticity": "Elasticity measures how responsive one economic variable is to a change in another variable.",
                "demand": "Demand describes the quantities consumers are willing and able to buy at different prices, other things equal.",
                "demand and supply": "Demand and supply jointly determine the market equilibrium price and quantity.",
                "monopoly": "A monopoly is a market with a single seller and barriers that prevent effective entry by competitors.",
                "perfect competition": "Perfect competition has many price-taking firms selling an identical product with low barriers to entry.",
                "externalities": "An externality is a cost or benefit from an activity that affects people not directly involved in the transaction.",
                "oligopoly": "An oligopoly is a market dominated by a small number of interdependent firms.",
            }
            for index in range(count):
                chunk = chunks[index % len(chunks)] if chunks else None
                content = str(chunk.get("content", "")) if chunk else ""
                source_policy = str(payload.get("source_policy", "reference_guided"))
                clean_sentences = [
                    part.strip() + "."
                    for part in re.split(r"(?<=[.!?])\s+|\n+", content)
                    if 40 <= len(part.strip()) <= 320
                    and "/uni" not in part.casefold()
                    and ".indd" not in part.casefold()
                    and "chapter" not in part.casefold()
                ]
                expected = (
                    clean_sentences[0]
                    if source_policy == "material_strict" and clean_sentences
                    else safe_definitions.get(
                        concept.casefold(),
                        f"A complete response accurately defines {concept} and states one logically connected implication.",
                    )
                )
                options = []
                prompt = f"Define {concept} and state one practical implication."
                if count > 1:
                    prompt = f"{prompt} Version {index + 1}: use a different example from earlier responses."
                if question_type == "true_false":
                    prompt = f"True or false: This statement is a valid description of {concept}: {expected}"
                    expected = "true"
                    options = ["true", "false"]
                elif question_type in {"single_choice", "multiple_choice"}:
                    prompt = f"Which option best describes {concept}?"
                    options = [expected, f"{concept} means that all choices have identical outcomes.", f"{concept} applies only when no trade-offs exist."]
                questions.append({
                    "learning_objective": f"Define and apply {concept}.",
                    "prompt": prompt,
                    "expected_answer": expected,
                    "solution_steps": [f"State the definition of {concept}.", "Connect the definition to one implication or example."],
                    "rubric_points": ["Accurate definition", "Relevant implication or example"],
                    "question_type": question_type,
                    "options": options,
                    "evidence_query": concept,
                    "source_chunk_ids": [int(chunk["id"])] if chunk else [],
                    "difficulty_profile": profile,
                    "expected_minutes": {1: 2, 2: 5, 3: 9, 4: 15, 5: 22}[level],
                })
            return {"questions": questions}
        if "assessments" in properties:
            assessments = []
            for index, question in enumerate(payload.get("questions", [])):
                profile = question.get("difficulty_profile", {})
                score = difficulty_score(profile)
                assessments.append({
                    "index": index,
                    "difficulty_profile": profile,
                    "difficulty_score": score,
                    "difficulty_level": difficulty_level(score),
                    "expected_minutes": int(question.get("expected_minutes", 5)),
                    "rationale": "Deterministic review of reasoning depth, integration, novelty, and workload.",
                    "ambiguity_detected": False,
                    "within_course_scope": True,
                })
            return {"assessments": assessments}
        expected_original = str(payload.get("expected_answer", "")).strip()
        expected = expected_original.casefold()
        answer = str(payload.get("answer", "")).strip().casefold()
        confidence = float(payload.get("confidence", 0.5))
        language = "zh" if payload.get("response_language") == "zh" else "en"
        correct = bool(expected) and (answer == expected or expected in answer)
        if correct and confidence < 0.45:
            error_type, delta, action = "guessing", 0.03, "retry"
            feedback = (
                f"正确答案是：{expected_original}。你的答案正确，但信心较低；请复述关键理由来巩固理解。"
                if language == "zh"
                else f"The correct answer is: {expected_original}. Your answer is correct, but your confidence is low; restate the key reason to reinforce it."
            )
            score = 0.8
        elif correct:
            error_type, delta, action = "none", 0.12, "advance"
            feedback = (
                f"正确答案是：{expected_original}。你的回答与标准答案一致，核心概念运用正确。"
                if language == "zh"
                else f"The correct answer is: {expected_original}. Your response matches the expected answer and applies the core concept correctly."
            )
            score = 1.0
        else:
            error_type = payload.get("forced_error_type") or "conceptual_misconception"
            delta, action, score = -0.08, "show_explanation", 0.0
            feedback = (
                f"正确答案是：{expected_original}。你的回答与标准答案不一致；请重新对照核心定义和题目条件，确认每一步推导。"
                if language == "zh"
                else f"The correct answer is: {expected_original}. Your response does not match it; compare the core definition with the question conditions and verify each step."
            )
        return {
            "is_correct": correct,
            "score": score,
            "confidence": confidence,
            "error_type": error_type,
            "evidence": payload.get("evidence", []),
            "feedback": feedback,
            "mastery_delta": delta,
            "next_action": action,
            "conclusion_correct": correct,
            "key_steps_score": 1.0 if correct else 0.0,
            "explanation_score": 1.0 if correct and len(answer.split()) >= 3 else (0.5 if correct else 0.0),
            "fatal_error": not correct,
        }
