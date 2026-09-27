from __future__ import annotations

from sqlalchemy import select

from models import AgentRun, MasteryRecord
from schemas import NextAction
from services.agent_service import AgentService
from services.database import session_scope
from services.llm import BaseLLMProvider


class InvalidProvider(BaseLLMProvider):
    def __init__(self):
        self.calls = 0

    def generate_json(self, **kwargs):
        self.calls += 1
        return {"not": "an evaluation"}


def test_invalid_provider_retries_once_and_falls_back(db_url):
    provider = InvalidProvider()
    with session_scope() as session:
        decision = AgentService(session, provider).submit_attempt(
            attempt_id="invalid-provider", question_id="demo-q1", answer="x", confidence=0.5
        )
        run = session.scalar(select(AgentRun).where(AgentRun.operation == "study_attempt"))
        assert provider.calls == 2
        assert decision.evaluation.next_action == NextAction.retry
        assert decision.evaluation.mastery_delta == 0
        assert decision.evaluation.fatal_error is False
        assert run.status == "degraded"
        assert "ValidationError" in run.error
        record = session.scalar(select(MasteryRecord).where(
            MasteryRecord.attempt_id == "invalid-provider"
        ))
        assert record and record.mastery == 0.25 and record.delta == 0
