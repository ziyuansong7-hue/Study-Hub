from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProviderError(RuntimeError):
    pass


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return parsed JSON matching the requested schema."""
        raise NotImplementedError
