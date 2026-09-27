from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_demo import seed_demo
from services.agent_service import AgentService
from services.database import configure_database, session_scope
from services.llm import FakeProvider


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db")
    seed_demo(database_url)
    configure_database(database_url)
    scenarios = [
        {"attempt_id": "demo-flow-hint", "question_id": "demo-q7", "answer": "two", "confidence": 0.8},
        {"attempt_id": "demo-flow-correct", "question_id": "demo-q7", "answer": "exactly one", "confidence": 0.9},
        {"attempt_id": "demo-flow-guess", "question_id": "demo-q8", "answer": "base case and inductive step", "confidence": 0.2},
    ]
    print("StudyHub deterministic demo (re-running is idempotent by attempt_id)")
    with session_scope() as session:
        service = AgentService(session, FakeProvider())
        for scenario in scenarios:
            decision = service.submit_attempt(**scenario)
            print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
