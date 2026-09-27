from __future__ import annotations

import json

from sqlalchemy import func, select

from models import Concept, Course, Question
from schemas import Evaluation
from scripts.seed_demo import seed_demo
from services.database import session_scope
from services.llm import FakeProvider


def test_seed_is_idempotent(db_url):
    seed_demo(db_url)
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Course)) == 1
        assert session.scalar(select(func.count()).select_from(Concept)) == 12
        assert session.scalar(select(func.count()).select_from(Question)) == 10


def test_fake_provider_returns_valid_evaluation(db_url):
    provider = FakeProvider()
    result = provider.generate_json(
        system="test",
        user=json.dumps({
            "expected_answer": "exactly one", "answer": "exactly one", "confidence": 0.9,
            "evidence": [{"document": "demo.md", "page": 1, "quote": "A function maps..."}],
        }),
        schema=Evaluation.model_json_schema(),
    )
    parsed = Evaluation.model_validate(result)
    assert parsed.is_correct is True
    assert parsed.mastery_delta > 0
