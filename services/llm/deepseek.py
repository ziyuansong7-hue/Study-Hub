from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from services.llm.base import BaseLLMProvider, LLMProviderError


class DeepSeekProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = (
            base_url
            or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")

        if not self.api_key:
            raise LLMProviderError("DEEPSEEK_API_KEY is required")

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_text = json.dumps(schema, ensure_ascii=False)
        json_instruction = (
            f"{system}\n\n"
            "Return one valid JSON object only. "
            "Do not use Markdown code fences.\n"
            f"The JSON must match this schema:\n{schema_text}"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": json_instruction},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 2048,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise LLMProviderError(
                f"DeepSeek HTTP {exc.code}: {error_body[:500]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMProviderError(f"DeepSeek request failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            if not content:
                raise ValueError("DeepSeek returned empty content")
            return json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMProviderError("DeepSeek returned invalid JSON") from exc
