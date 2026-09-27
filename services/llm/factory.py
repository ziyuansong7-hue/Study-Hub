from __future__ import annotations

import os

from services.llm.base import BaseLLMProvider
from services.llm.deepseek import DeepSeekProvider
from services.llm.fake import FakeProvider
from services.llm.real import RealProvider


def configured_provider_name() -> str:
    choice = os.getenv("STUDYHUB_LLM_PROVIDER", "fake").lower()
    return "deepseek" if choice == "deepseek" else "openai" if choice in {"openai", "real"} else "fake"


def get_llm_provider() -> BaseLLMProvider:
    choice = configured_provider_name()

    if choice == "deepseek":
        return DeepSeekProvider()

    if choice in {"openai", "real"}:
        return RealProvider()

    return FakeProvider()
