from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Attempt, Concept, Course, MasteryRecord, Question, ReviewTask


class LearningRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_course(self, code: str) -> Course | None:
        return self.session.scalar(select(Course).where(Course.code == code))

    def get_question(self, external_id: str, course_id: int | None = None) -> Question | None:
        query = select(Question).where(Question.external_id == external_id)
        if course_id is not None:
            query = query.where(Question.course_id == course_id)
        return self.session.scalar(query.order_by(Question.id).limit(1))

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        return self.session.scalar(select(Attempt).where(Attempt.attempt_id == attempt_id))

    def latest_mastery(self, concept_id: int, student_id: str = "default") -> float:
        row = self.session.scalar(
            select(MasteryRecord)
            .where(MasteryRecord.concept_id == concept_id, MasteryRecord.student_id == student_id)
            .order_by(MasteryRecord.created_at.desc(), MasteryRecord.id.desc())
        )
        return row.mastery if row else 0.25

    def save_attempt(self, attempt: Attempt, mastery: MasteryRecord) -> tuple[Attempt, bool]:
        existing = self.get_attempt(attempt.attempt_id)
        if existing:
            return existing, False
        self.session.add(attempt)
        self.session.flush()
        mastery.attempt_id = attempt.attempt_id
        self.session.add(mastery)
        self.session.flush()
        return attempt, True

    def schedule_review(
        self, concept_id: int, due_date: date, reason: str, student_id: str = "default"
    ) -> tuple[ReviewTask, bool]:
        existing = self.session.scalar(
            select(ReviewTask).where(
                ReviewTask.student_id == student_id,
                ReviewTask.concept_id == concept_id,
                ReviewTask.due_date == due_date,
            )
        )
        if existing:
            return existing, False
        task = ReviewTask(
            student_id=student_id, concept_id=concept_id, due_date=due_date, reason=reason
        )
        self.session.add(task)
        self.session.flush()
        return task, True

    def concepts(self, course_id: int) -> list[Concept]:
        return list(self.session.scalars(select(Concept).where(Concept.course_id == course_id)))
