from __future__ import annotations

import re
from dataclasses import dataclass

BAD_MARKERS = ("/uni", ".indd", "\ufffd")


@dataclass(frozen=True)
class ChunkQuality:
    content: str
    chunk_type: str
    score: float
    flags: list[str]
    usable: bool


def normalize_extracted_text(value: str) -> str:
    """Repair common PDF extraction artifacts without inventing missing content."""
    value = value.replace("\ufb01", "fi").replace("\ufb02", "fl")
    value = value.replace("\u00ad", "").replace("\u200b", "")
    value = re.sub(r"(?<=\w)\ufffd+(?=s\b)", "’", value)
    value = re.sub(r"(?<=\w)\ufffd+(?=\w)", "-", value)
    lines = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if ".indd" in line.casefold():
            continue
        if re.fullmatch(r"/uni[0-9a-f]{4,8}", line, flags=re.I):
            continue
        lines.append(line)
    value = "\n".join(lines)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def assess_chunk(content: str, section: str | None = None) -> ChunkQuality:
    cleaned = normalize_extracted_text(content)
    lower = cleaned.casefold()
    flags: list[str] = []
    score = 1.0

    chapter_lines = re.findall(r"(?im)^\s*(?:\d+\s+)?chapter\s+\d+\b", cleaned)
    numbered_toc_lines = re.findall(r"(?im)^.{3,90}\s+\d{1,4}\s*$", cleaned)
    is_toc = "detailed contents" in lower or len(chapter_lines) >= 3 or (
        len(numbered_toc_lines) >= 6 and re.search(r"(?im)^\s*(part|contents?)\b", cleaned)
    )
    if is_toc:
        flags.append("table_of_contents")
        score -= 0.65

    replacement_count = cleaned.count("\ufffd")
    if replacement_count:
        flags.append("replacement_characters")
        score -= min(0.6, 0.08 * replacement_count)
    if "/uni" in lower:
        flags.append("font_marker")
        score -= 0.6
    if ".indd" in lower:
        flags.append("layout_artifact")
        score -= 0.45
    if len(cleaned) < 40:
        flags.append("very_short")
        score -= 0.55
    elif len(cleaned) < 100:
        flags.append("short_fragment")
        score -= 0.18
    alpha_count = sum(character.isalpha() for character in cleaned)
    if cleaned and alpha_count / max(len(cleaned), 1) < 0.35:
        flags.append("low_text_ratio")
        score -= 0.35

    exercise_markers = len(re.findall(r"(?im)^\s*\d+[.)]\s+", cleaned))
    is_exercise = exercise_markers >= 2 or "review quiz" in lower or "problems and applications" in lower
    chunk_type = "table_of_contents" if is_toc else "exercise" if is_exercise else "content"
    if score < 0.35:
        chunk_type = "noise"
    score = max(0.0, min(1.0, round(score, 2)))
    usable = score >= 0.55 and chunk_type not in {"table_of_contents", "noise"}
    return ChunkQuality(cleaned, chunk_type, score, flags, usable)


def question_quality(
    prompt: str,
    expected_answer: str,
    *,
    options: list[str] | None = None,
) -> tuple[str, list[str]]:
    combined = f"{prompt}\n{expected_answer}\n{' '.join(options or [])}"
    lower = combined.casefold()
    flags: list[str] = []
    if any(marker in lower for marker in BAD_MARKERS):
        flags.append("extraction_artifact")
    if len(prompt.strip()) < 12:
        flags.append("question_too_short")
    normalized_expected = expected_answer.strip().casefold()
    normalized_options = [value.strip().casefold() for value in (options or [])]
    if (
        len(expected_answer.strip()) < 8
        and normalized_expected not in {"true", "false"}
        and normalized_expected not in normalized_options
    ):
        flags.append("answer_too_short")
    if expected_answer.strip().endswith((",", ";", ":")):
        flags.append("incomplete_answer_fragment")
    if expected_answer.count("\n") >= 8:
        flags.append("raw_excerpt_as_answer")
    if len(re.findall(r"(?im)^\s*chapter\s+\d+", expected_answer)) >= 2:
        flags.append("table_of_contents_answer")
    if re.search(r"(?i)\b(part one|table of contents)\b", expected_answer):
        flags.append("front_matter_answer")
    if len(set(re.findall(r"[a-z]{3,}", prompt.casefold()))) < 2:
        flags.append("low_semantic_content")
    if normalized_options and "none of the above" in normalized_options:
        substantive = [value for value in normalized_options if not value.startswith("not ") and value != "none of the above"]
        if len(substantive) < 2:
            flags.append("placeholder_choice_set")
    return ("quarantined" if flags else "passed", flags)
