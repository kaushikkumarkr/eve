from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass(frozen=True)
class LLMResult:
    value: Any
    provider: str
    model: str
    raw_text: str


class OpenAIResearchProvider:
    """Optional OpenAI-backed provider; never used by default tests."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI research mode")
        from openai import OpenAI

        self.client = OpenAI(api_key=key)
        self.model = model or settings.openai_model

    def generate_json(self, instruction: str, context: str) -> LLMResult:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": "Return only valid JSON. Do not invent evidence. Mark generated claims as inferred."},
                {"role": "user", "content": f"{instruction}\n\nAuthorized source context:\n{context}"},
            ],
        )
        raw = response.output_text.strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI research response was not valid JSON") from exc
        return LLMResult(value=value, provider="openai", model=self.model, raw_text=raw)


def get_research_provider() -> OpenAIResearchProvider | None:
    if settings.research_mode.lower() == "openai":
        return OpenAIResearchProvider()
    return None
