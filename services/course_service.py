from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Course

VALID_LANGUAGES = {"en", "zh", "mixed"}
VALID_TERMINOLOGY_POLICIES = {"preserve_source", "english_terms", "translated_terms"}


class CourseService:
    def __init__(self, session: Session):
        self.session = session

    def list_active(self) -> list[Course]:
        return list(self.session.scalars(
            select(Course).where(Course.archived_at.is_(None)).order_by(Course.last_studied_at.desc(), Course.title)
        ))

    def create(
        self,
        *,
        title: str,
        code: str = "",
        material_language: str = "en",
        explanation_language: str = "en",
        terminology_policy: str = "preserve_source",
        color: str = "#ff9000",
        icon: str = "book",
        description: str = "",
        external_ai_allowed: bool = False,
    ) -> Course:
        title = title.strip()
        if not title:
            raise ValueError("course name is required")
        clean_code = re.sub(r"[^A-Za-z0-9_-]", "", code.strip().upper())[:32]
        generated = not bool(clean_code)
        clean_code = clean_code or f"COURSE-{uuid.uuid4().hex[:8].upper()}"
        if self.session.scalar(select(Course).where(Course.code == clean_code)):
            raise ValueError("course code already exists")
        if material_language not in VALID_LANGUAGES or explanation_language not in VALID_LANGUAGES:
            raise ValueError("invalid course language")
        if terminology_policy not in VALID_TERMINOLOGY_POLICIES:
            raise ValueError("invalid terminology policy")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#ff9000"
        course = Course(
            code=clean_code,
            code_is_generated=generated,
            title=title[:255],
            description=description.strip()[:2000],
            material_language=material_language,
            explanation_language=explanation_language,
            terminology_policy=terminology_policy,
            color=color,
            icon=icon.strip()[:32] or "book",
            external_ai_allowed=bool(external_ai_allowed),
        )
        self.session.add(course)
        self.session.flush()
        return course

    def edit(self, course_id: int, **values) -> Course:
        course = self.session.get(Course, course_id)
        if not course:
            raise ValueError("course not found")
        title = str(values.get("title", course.title)).strip()
        if not title:
            raise ValueError("course name is required")
        course.title = title[:255]
        course.description = str(values.get("description", course.description)).strip()[:2000]
        for field in ("material_language", "explanation_language"):
            value = str(values.get(field, getattr(course, field)))
            if value not in VALID_LANGUAGES:
                raise ValueError("invalid course language")
            setattr(course, field, value)
        policy = str(values.get("terminology_policy", course.terminology_policy))
        if policy not in VALID_TERMINOLOGY_POLICIES:
            raise ValueError("invalid terminology policy")
        course.terminology_policy = policy
        color = str(values.get("color", course.color))
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            course.color = color
        course.icon = str(values.get("icon", course.icon)).strip()[:32] or "book"
        if "external_ai_allowed" in values:
            course.external_ai_allowed = bool(values["external_ai_allowed"])
        self.session.flush()
        return course

    def set_archived(self, course_id: int, archived: bool) -> Course:
        course = self.session.get(Course, course_id)
        if not course:
            raise ValueError("course not found")
        course.archived_at = datetime.now(timezone.utc) if archived else None
        self.session.flush()
        return course
