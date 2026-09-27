from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Attempt, Concept, Course, MasteryRecord, Question, StudyTask

VALID_MODES = {"focus", "practice", "drill", "review"}
VALID_TARGET_TYPES = {"questions", "minutes"}


class StudyPlanService:
    def __init__(self, session: Session, student_id: str = "default"):
        self.session = session
        self.student_id = student_id

    def _task(self, task_id: int) -> StudyTask:
        task = self.session.scalar(select(StudyTask).where(
            StudyTask.id == task_id, StudyTask.student_id == self.student_id
        ))
        if not task:
            raise ValueError("study task not found")
        return task

    def _validate_scope(self, course_id: int, concept_id: int | None) -> tuple[Course, Concept | None]:
        course = self.session.get(Course, course_id)
        if not course:
            raise ValueError("course not found")
        concept = self.session.get(Concept, concept_id) if concept_id else None
        if concept and concept.course_id != course.id:
            raise ValueError("concept does not belong to the selected course")
        return course, concept

    def create_manual(
        self,
        *,
        course_id: int,
        concept_id: int | None,
        title: str,
        mode: str,
        target_type: str,
        target_value: int,
        due_date: date,
        notes: str = "",
    ) -> StudyTask:
        course, concept = self._validate_scope(course_id, concept_id)
        if mode not in VALID_MODES:
            raise ValueError("invalid study mode")
        mode = "practice" if mode == "drill" else mode
        if target_type not in VALID_TARGET_TYPES:
            raise ValueError("invalid target type")
        if not 1 <= target_value <= 999:
            raise ValueError("target value must be between 1 and 999")
        clean_title = title.strip() or (concept.name if concept else course.title)
        task = StudyTask(
            student_id=self.student_id,
            course_id=course.id,
            concept_id=concept.id if concept else None,
            title=clean_title[:255],
            mode=mode,
            target_type=target_type,
            target_value=target_value,
            due_date=due_date,
            status="pending",
            source="manual",
            notes=notes.strip()[:2000],
        )
        self.session.add(task)
        self.session.flush()
        return task

    def edit(self, task_id: int, **values) -> StudyTask:
        task = self._task(task_id)
        course_id = int(values.get("course_id", task.course_id))
        raw_concept = values.get("concept_id", task.concept_id)
        concept_id = int(raw_concept) if raw_concept not in {None, ""} else None
        self._validate_scope(course_id, concept_id)
        mode = str(values.get("mode", task.mode))
        target_type = str(values.get("target_type", task.target_type))
        target_value = int(values.get("target_value", task.target_value))
        if mode not in VALID_MODES or target_type not in VALID_TARGET_TYPES:
            raise ValueError("invalid task settings")
        mode = "practice" if mode == "drill" else mode
        if not 1 <= target_value <= 999:
            raise ValueError("target value must be between 1 and 999")
        task.course_id = course_id
        task.concept_id = concept_id
        task.title = str(values.get("title", task.title)).strip()[:255] or task.title
        task.mode = mode
        task.target_type = target_type
        task.target_value = target_value
        task.due_date = values.get("due_date", task.due_date)
        task.notes = str(values.get("notes", task.notes)).strip()[:2000]
        self.session.flush()
        return task

    def action(self, task_id: int, action: str) -> StudyTask:
        task = self._task(task_id)
        if action == "complete":
            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)
        elif action == "reopen":
            task.status = "pending"
            task.completed_at = None
        elif action == "delete" or action == "dismiss":
            task.status = "dismissed"
        elif action == "postpone":
            task.due_date = date.today() + timedelta(days=1)
        elif action == "accept":
            task.status = "pending"
            task.due_date = date.today()
        else:
            raise ValueError("unknown task action")
        self.session.flush()
        return task

    def timer_action(self, task_id: int, action: str) -> StudyTask:
        task = self._task(task_id)
        if task.target_type != "minutes":
            raise ValueError("timer is only available for minute-based tasks")
        now = datetime.now(timezone.utc)
        if action == "start":
            if task.timer_started_at is None:
                task.timer_started_at = now
                task.status = "in_progress"
        elif action in {"pause", "finish"}:
            if task.timer_started_at is not None:
                started = task.timer_started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                task.accumulated_seconds += max(0, int((now - started).total_seconds()))
                task.timer_started_at = None
            task.progress_value = min(task.target_value, task.accumulated_seconds // 60)
            if action == "finish" or task.progress_value >= task.target_value:
                task.status = "completed"
                task.completed_at = now
            else:
                task.status = "pending"
        elif action == "reset":
            task.timer_started_at = None
            task.accumulated_seconds = 0
            task.progress_value = 0
            task.status = "pending"
            task.completed_at = None
        else:
            raise ValueError("unknown timer action")
        self.session.flush()
        return task

    def ensure_ai_recommendations(self, limit: int = 3, threshold: float = 0.55) -> int:
        records = list(self.session.scalars(select(MasteryRecord).where(
            MasteryRecord.student_id == self.student_id
        ).order_by(MasteryRecord.created_at.desc(), MasteryRecord.id.desc())))
        seen: set[int] = set()
        created = 0
        today = date.today()
        for record in records:
            if record.concept_id in seen:
                continue
            seen.add(record.concept_id)
            if record.mastery >= threshold:
                continue
            concept = self.session.get(Concept, record.concept_id)
            if not concept:
                continue
            course = concept.course
            latest_attempt = self.session.scalar(
                select(Attempt)
                .join(Question, Question.id == Attempt.question_id)
                .where(
                    Attempt.student_id == self.student_id,
                    Question.concept_id == concept.id,
                )
                .order_by(Attempt.created_at.desc(), Attempt.id.desc())
            )
            if course.explanation_language == "zh":
                error_note = (
                    f" 最近错误类型：{latest_attempt.error_type}。"
                    if latest_attempt and not latest_attempt.is_correct else ""
                )
                recommendation_note = f"当前掌握度估计：{record.mastery:.0%}。{error_note}"
            else:
                error_note = (
                    f" Recent error: {latest_attempt.error_type}."
                    if latest_attempt and not latest_attempt.is_correct else ""
                )
                recommendation_note = f"Current mastery estimate: {record.mastery:.0%}.{error_note}"
            key = f"ai:{self.student_id}:{concept.id}:{today.isoformat()}"
            if self.session.scalar(select(StudyTask).where(StudyTask.recommendation_key == key)):
                continue
            self.session.add(StudyTask(
                student_id=self.student_id,
                course_id=concept.course_id,
                concept_id=concept.id,
                title=f"Practice {concept.name}",
                mode="focus",
                target_type="questions",
                target_value=5,
                due_date=today,
                status="suggested",
                source="ai_recommendation",
                notes=recommendation_note,
                recommendation_key=key,
            ))
            created += 1
            if created >= limit:
                break
        self.session.flush()
        return created

    def dashboard(self, course_id: int | None = None) -> dict:
        today = date.today()
        task_query = select(StudyTask).where(
            StudyTask.student_id == self.student_id,
            StudyTask.status.in_(["pending", "in_progress", "completed"]),
            StudyTask.due_date <= today,
        )
        recommendation_query = select(StudyTask).where(
            StudyTask.student_id == self.student_id,
            StudyTask.status == "suggested",
            StudyTask.due_date <= today,
        )
        if course_id:
            task_query = task_query.where(StudyTask.course_id == course_id)
            recommendation_query = recommendation_query.where(StudyTask.course_id == course_id)
        tasks = list(self.session.scalars(
            task_query.order_by(StudyTask.status, StudyTask.due_date, StudyTask.created_at)
        ))
        recommendations = list(self.session.scalars(
            recommendation_query.order_by(StudyTask.created_at).limit(3)
        ))
        completed = sum(task.status == "completed" for task in tasks)
        return {
            "tasks": tasks,
            "recommendations": recommendations,
            "completed": completed,
            "total": len(tasks),
            "percent": round((completed / len(tasks)) * 100) if tasks else 0,
        }

    def question_for_task(self, task_id: int) -> Question | None:
        task = self._task(task_id)
        query = select(Question).where(
            Question.course_id == task.course_id,
            Question.is_active.is_(True),
            Question.moderation_status == "approved",
            Question.quality_status == "passed",
        )
        if task.concept_id:
            query = query.where(Question.concept_id == task.concept_id)
        return self.session.scalar(query.order_by(Question.id).limit(1))

    def schedule_concept_review(self, concept_id: int, due_date: date | None = None) -> tuple[StudyTask, bool]:
        concept = self.session.get(Concept, concept_id)
        if not concept:
            raise ValueError("concept not found")
        due = due_date or (date.today() + timedelta(days=1))
        key = f"review-plan:{self.student_id}:{concept.id}:{due.isoformat()}"
        existing = self.session.scalar(select(StudyTask).where(StudyTask.recommendation_key == key))
        if existing:
            return existing, False
        task = StudyTask(
            student_id=self.student_id,
            course_id=concept.course_id,
            concept_id=concept.id,
            title=f"Review {concept.name}",
            mode="review",
            target_type="questions",
            target_value=5,
            due_date=due,
            status="pending",
            source="manual",
            notes="Scheduled from a Study Session result.",
            recommendation_key=key,
        )
        self.session.add(task)
        self.session.flush()
        return task, True
