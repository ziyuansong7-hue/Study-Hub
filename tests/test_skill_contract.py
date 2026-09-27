from __future__ import annotations

from pathlib import Path


def test_study_coach_skill_contract_is_complete():
    root = Path(__file__).resolve().parents[1] / "skills" / "study-coach"
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: study-coach\n")
    assert "description:" in text.split("---", 2)[1]
    for required in [
        "get_student_state", "search_course_material", "save_attempt", "schedule_review",
        "Prohibited behavior", "Completion conditions", "课程材料中未找到可靠依据",
    ]:
        assert required in text
    for reference in ["error-taxonomy.md", "mastery-rules.md", "output-schema.md"]:
        path = root / "references" / reference
        assert path.is_file() and path.read_text(encoding="utf-8").strip()
