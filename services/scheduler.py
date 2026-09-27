from __future__ import annotations

import logging
from datetime import date, timedelta
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from models import MasteryRecord
from repositories import LearningRepository
from services.database import configure_database, init_database, session_scope

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
_lock = Lock()


def create_daily_reviews(
    database_url: str = "sqlite:///instance/studyhub.db",
    *,
    student_id: str = "default",
    threshold: float = 0.55,
    due_date: date | None = None,
) -> dict[str, int]:
    configure_database(database_url)
    init_database()
    target = due_date or (date.today() + timedelta(days=1))
    created = 0
    considered: set[int] = set()
    with session_scope() as session:
        records = list(session.scalars(select(MasteryRecord).where(
            MasteryRecord.student_id == student_id
        ).order_by(MasteryRecord.created_at.desc(), MasteryRecord.id.desc())))
        repo = LearningRepository(session)
        for record in records:
            if record.concept_id in considered:
                continue
            considered.add(record.concept_id)
            if record.mastery < threshold:
                _, was_created = repo.schedule_review(
                    record.concept_id, target,
                    f"mastery {record.mastery:.2f} below threshold {threshold:.2f}", student_id,
                )
                created += int(was_created)
    logger.info("daily review job considered=%s created=%s due=%s", len(considered), created, target)
    return {"considered": len(considered), "created": created}


def start_scheduler(database_url: str) -> BackgroundScheduler:
    global _scheduler
    with _lock:
        if _scheduler and _scheduler.running:
            return _scheduler
        scheduler = BackgroundScheduler(timezone=None)
        scheduler.add_job(
            create_daily_reviews,
            trigger="cron", hour=2, minute=0,
            kwargs={"database_url": database_url},
            id="studyhub-daily-review", replace_existing=True,
            coalesce=True, max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler
        return scheduler
