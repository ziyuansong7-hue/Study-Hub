from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    AgentRun,
    Attempt,
    Concept,
    Course,
    Question,
    ReviewTask,
    SessionItem,
    StudentConceptState,
    StudySession,
    StudyTask,
)
from repositories import LearningRepository
from services.difficulty import adaptive_level

VALID_MODES = {"focus", "practice", "drill", "review"}
VALID_QUESTION_TYPES = {
    "any", "single_choice", "multiple_choice", "short_answer", "true_false", "calculation"
}


class StudySessionService:
    def __init__(self, session: Session, student_id: str = "default"):
        self.session = session
        self.student_id = student_id
        self.repo = LearningRepository(session)

    def create(
        self,
        *,
        course_id: int,
        mode: str,
        concept_id: int | None,
        difficulty: float,
        target_count: int,
        question_type: str,
        concept_ids: list[int] | None = None,
        source_policy: str = "reference_guided",
        material_only: bool = False,
        study_task_id: int | None = None,
        difficulty_level: int | None = None,
        parent_session_id: int | None = None,
    ) -> StudySession:
        course = self.session.get(Course, course_id)
        if not course or course.archived_at is not None:
            raise ValueError("active course not found")
        if mode not in VALID_MODES:
            raise ValueError("invalid study mode")
        mode = "practice" if mode == "drill" else mode
        if question_type not in VALID_QUESTION_TYPES:
            raise ValueError("invalid question type")
        if source_policy not in {"reference_guided", "material_strict", "exam_style", "any"}:
            raise ValueError("invalid question source policy")
        if not 1 <= target_count <= 50:
            raise ValueError("question count must be between 1 and 50")
        selected_ids = list(dict.fromkeys(
            value for value in ([concept_id] if concept_id else []) + (concept_ids or []) if value
        ))
        selected_concepts = list(self.session.scalars(
            select(Concept).where(Concept.id.in_(selected_ids or [-1]))
        ))
        if len(selected_concepts) != len(selected_ids) or any(value.course_id != course.id for value in selected_concepts):
            raise ValueError("one or more concepts do not belong to the selected course")
        if mode == "focus" and len(selected_concepts) != 1:
            raise ValueError("focus mode requires one concept")
        concept = selected_concepts[0] if len(selected_concepts) == 1 else None
        if study_task_id:
            task = self.session.get(StudyTask, study_task_id)
            if not task or task.course_id != course.id:
                raise ValueError("study task does not belong to the selected course")
        selected_level = difficulty_level
        if selected_level in {None, 0}:
            states = list(self.session.scalars(select(StudentConceptState).where(
                StudentConceptState.student_id == self.student_id,
                StudentConceptState.concept_id.in_(selected_ids or [-1]),
            )))
            state = states[0] if len(states) == 1 else None
            aggregate_mastery = sum(value.mastery for value in states) / len(states) if states else 0.25
            selected_level = adaptive_level(
                mastery=state.mastery if state else aggregate_mastery,
                correct_streak=state.correct_streak if state else 0,
                failure_streak=state.failure_streak if state else 0,
            )
        selected_level = max(1, min(5, int(selected_level)))
        study_session = StudySession(
            student_id=self.student_id,
            course_id=course.id,
            concept_id=concept.id if concept else None,
            concept_ids=selected_ids,
            study_task_id=study_task_id,
            mode=mode,
            question_type=question_type,
            source_policy=source_policy,
            difficulty=max(0.0, min(1.0, float(difficulty))),
            difficulty_level=selected_level,
            target_count=target_count,
            material_only=material_only,
            parent_session_id=parent_session_id,
        )
        self.session.add(study_session)
        self.session.flush()
        questions = self._select_questions(study_session)
        unique: list[Question] = []
        seen: set[int] = set()
        for question in questions:
            if question.id not in seen:
                unique.append(question)
                seen.add(question.id)
            if len(unique) >= target_count:
                break
        if not unique:
            raise ValueError("no approved questions are available; generate and approve questions in the Question Bank first")
        study_session.target_count = len(unique)
        for position, question in enumerate(unique, start=1):
            self.session.add(SessionItem(
                study_session_id=study_session.id,
                question_id=question.id,
                position=position,
            ))
        course.last_studied_at = datetime.now(timezone.utc)
        self.session.flush()
        return study_session

    def _base_questions(self, study_session: StudySession) -> list[Question]:
        query = select(Question).where(
            Question.course_id == study_session.course_id,
            Question.is_active.is_(True),
            Question.moderation_status == "approved",
            Question.quality_status == "passed",
        )
        selected_ids = study_session.concept_ids or (
            [study_session.concept_id] if study_session.concept_id else []
        )
        if selected_ids:
            query = query.where(Question.concept_id.in_(selected_ids))
        if study_session.question_type != "any":
            query = query.where(Question.question_type == study_session.question_type)
        if study_session.material_only:
            query = query.where(Question.source_type == "material_based")
        elif study_session.source_policy not in {"", "any"}:
            query = query.where(Question.source_policy == study_session.source_policy)
        return list(self.session.scalars(query.order_by(Question.id)))

    def _select_questions(self, study_session: StudySession) -> list[Question]:
        questions = self._base_questions(study_session)
        recent_question_ids = list(self.session.scalars(
            select(Attempt.question_id)
            .where(Attempt.student_id == self.student_id)
            .order_by(Attempt.created_at.desc(), Attempt.id.desc())
            .limit(20)
        ))
        recent_rank = {question_id: index for index, question_id in enumerate(recent_question_ids)}

        def rank(question: Question):
            recent_penalty = 1 if question.id in recent_rank else 0
            return (
                recent_penalty,
                abs(question.difficulty_level - study_session.difficulty_level),
                recent_rank.get(question.id, 999),
                question.id,
            )
        if study_session.mode == "focus":
            return sorted(questions, key=rank)[
                : study_session.target_count
            ]
        due_concepts = set(self.session.scalars(select(ReviewTask.concept_id).where(
            ReviewTask.student_id == self.student_id,
            ReviewTask.due_date <= date.today(),
            ReviewTask.status == "pending",
        )))
        wrong_question_ids = set(self.session.scalars(select(Attempt.question_id).where(
            Attempt.student_id == self.student_id,
            Attempt.is_correct.is_(False),
        )))
        latest_mastery = {
            question.concept_id: self.repo.latest_mastery(question.concept_id, self.student_id)
            for question in questions
        }
        buckets = {
            "weak": [q for q in questions if latest_mastery[q.concept_id] < 0.55],
            "due": [q for q in questions if q.concept_id in due_concepts],
            "mistake": [q for q in questions if q.id in wrong_question_ids],
            "mastered": [q for q in questions if latest_mastery[q.concept_id] >= 0.55],
        }
        if study_session.mode == "review":
            order = ["due", "mistake", "weak", "mastered"]
            ratios = [0.45, 0.30, 0.20, 0.05]
        else:
            order = ["weak", "due", "mistake", "mastered"]
            ratios = [0.40, 0.25, 0.20, 0.15]
        chosen: list[Question] = []
        seen: set[int] = set()
        selected_ids = study_session.concept_ids or []
        if study_session.mode == "practice" and len(selected_ids) > 1:
            # A multi-concept scope should be visible in the resulting session.
            # Seed one best-fit question per selected concept before filling the
            # remaining slots with adaptive weak/due/mistake priorities.
            for concept_id in selected_ids:
                candidates = [q for q in questions if q.concept_id == concept_id]
                if candidates:
                    question = min(candidates, key=rank)
                    chosen.append(question)
                    seen.add(question.id)
                if len(chosen) >= study_session.target_count:
                    break
        for bucket, ratio in zip(order, ratios, strict=True):
            quota = max(1, math.floor(study_session.target_count * ratio))
            ranked = sorted(buckets[bucket], key=rank)
            added = 0
            for question in ranked:
                if question.id not in seen:
                    chosen.append(question)
                    seen.add(question.id)
                    added += 1
                if added >= quota:
                    break
        for question in sorted(questions, key=rank):
            if question.id not in seen:
                chosen.append(question)
                seen.add(question.id)
            if len(chosen) >= study_session.target_count:
                break
        return chosen[: study_session.target_count]

    def get(self, session_key: str) -> StudySession:
        study_session = self.session.scalar(select(StudySession).where(
            StudySession.session_key == session_key,
            StudySession.student_id == self.student_id,
        ))
        if not study_session:
            raise ValueError("study session not found")
        return study_session

    @staticmethod
    def current_item(study_session: StudySession) -> SessionItem | None:
        return next((item for item in study_session.items if item.status == "pending"), None)

    def record_attempt(self, item: SessionItem, attempt_id: str) -> None:
        if item.status == "answered":
            return
        item.status = "answered"
        item.attempt_id = attempt_id
        study_session = item.study_session
        course = study_session.course
        course.last_studied_at = datetime.now(timezone.utc)
        if study_session.study_task:
            task = study_session.study_task
            task.progress_value = min(task.target_value, task.progress_value + 1)
            if task.target_type == "questions" and task.progress_value >= task.target_value:
                task.status = "completed"
                task.completed_at = datetime.now(timezone.utc)
        if not any(value.status == "pending" for value in study_session.items):
            study_session.status = "completed"
            study_session.completed_at = datetime.now(timezone.utc)
        self.session.flush()

    def skip_current(self, study_session: StudySession) -> SessionItem:
        item = self.current_item(study_session)
        if not item:
            raise ValueError("no question is available to skip")
        item.status = "skipped"
        self.session.add(AgentRun(
            run_id=str(uuid.uuid4()),
            student_id=self.student_id,
            operation="skip_question",
            input_summary={"session_id": study_session.session_key, "question_id": item.question.external_id},
            tools_used=[],
            decision={"action": "skip"},
            duration_ms=0,
            status="success",
        ))
        if not self.current_item(study_session):
            study_session.status = "completed"
            study_session.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        return item
