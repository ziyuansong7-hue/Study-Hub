from __future__ import annotations

from models import Course


def language_policy(course: Course, fallback_language: str = "en") -> str:
    explanation = course.explanation_language
    if explanation == "mixed":
        explanation = "zh" if fallback_language == "zh" else "en"
    output = "Chinese" if explanation == "zh" else "English"
    material = {"zh": "Chinese", "en": "English", "mixed": "mixed-language"}.get(
        course.material_language, "source-language"
    )
    terminology = {
        "preserve_source": "Preserve specialist terminology exactly as it appears in the source material.",
        "english_terms": "Write ordinary explanations in the output language, but keep specialist terms in English and optionally add a short translation in parentheses.",
        "translated_terms": "Use specialist terminology in the material's primary language and do not force an English translation.",
    }.get(course.terminology_policy, "Preserve specialist terminology from the source.")
    return (
        f"The material language is {material}. Write learner-facing explanations in {output}. "
        f"{terminology} Never translate quoted evidence."
    )
