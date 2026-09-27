from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from models import Attempt, MasteryRecord, Question, StudentConceptState
from services.database import configure_database, init_database, session_scope


def backfill(database_url: str = "sqlite:///instance/studyhub.db") -> dict[str, int]:
    configure_database(database_url)
    init_database()
    updated = 0
    with session_scope() as session:
        attempts = list(session.execute(
            select(Attempt, Question.concept_id)
            .join(Question, Question.id == Attempt.question_id)
            .order_by(Attempt.student_id, Question.concept_id, Attempt.created_at, Attempt.id)
        ))
        grouped: dict[tuple[str, int], list[Attempt]] = defaultdict(list)
        for attempt, concept_id in attempts:
            grouped[(attempt.student_id, concept_id)].append(attempt)
        for (student_id, concept_id), history in grouped.items():
            state = session.scalar(select(StudentConceptState).where(
                StudentConceptState.student_id == student_id,
                StudentConceptState.concept_id == concept_id,
            )) or StudentConceptState(student_id=student_id, concept_id=concept_id)
            session.add(state)
            latest_mastery = session.scalar(select(MasteryRecord).where(
                MasteryRecord.student_id == student_id,
                MasteryRecord.concept_id == concept_id,
            ).order_by(MasteryRecord.created_at.desc(), MasteryRecord.id.desc()))
            correct_streak = 0
            failure_streak = 0
            for attempt in reversed(history):
                if attempt.is_correct and failure_streak == 0:
                    correct_streak += 1
                elif not attempt.is_correct and correct_streak == 0:
                    failure_streak += 1
                else:
                    break
            state.mastery = latest_mastery.mastery if latest_mastery else 0.25
            state.last_practiced_at = history[-1].created_at
            state.correct_streak = correct_streak
            state.failure_streak = failure_streak
            state.average_confidence = sum(item.confidence for item in history) / len(history)
            state.attempts_count = len(history)
            state.last_error_type = history[-1].error_type
            state.needs_review = state.mastery < 0.55 or failure_streak > 0
            updated += 1
    return {"states_updated": updated}


if __name__ == "__main__":
    print(backfill(os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db")))
