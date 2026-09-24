from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import normalize_azure_endpoint, settings


@dataclass(frozen=True)
class LLMResult:
    value: Any
    provider: str
    model: str
    raw_text: str


class OpenAIResearchProvider:
    """Optional OpenAI or Azure OpenAI-backed research provider."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        azure_configured = bool(
            settings.azure_openai_endpoint
            and (settings.azure_openai_api_key or api_key)
            and settings.azure_openai_deployment
        )
        if azure_configured:
            from openai import AzureOpenAI

            self.client = AzureOpenAI(
                api_key=api_key or settings.azure_openai_api_key,
                azure_endpoint=normalize_azure_endpoint(settings.azure_openai_endpoint or ""),
                api_version=settings.azure_openai_api_version,
            )
            self.model = model or settings.azure_openai_deployment or ""
            self.provider = "azure_openai"
            self._uses_chat_completions = True
            return

        key = api_key or settings.openai_api_key
        if not key:
            raise RuntimeError(
                "OpenAI research mode requires either OPENAI_API_KEY or a complete "
                "Azure OpenAI configuration"
            )
        from openai import OpenAI

        self.client = OpenAI(api_key=key)
        self.model = model or settings.openai_model
        self.provider = "openai"
        self._uses_chat_completions = False

    def generate_json(self, instruction: str, context: str) -> LLMResult:
        system_message = (
            "Return only valid JSON. Do not invent evidence. "
            "Mark generated claims as inferred."
        )
        user_message = f"{instruction}\n\nAuthorized source context:\n{context}"
        if self._uses_chat_completions:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
                max_tokens=2000,
            )
            raw = (response.choices[0].message.content or "").strip()
        else:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
            )
            raw = response.output_text.strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI research response was not valid JSON") from exc
        return LLMResult(value=value, provider=self.provider, model=self.model, raw_text=raw)


def get_research_provider() -> OpenAIResearchProvider | None:
    if settings.research_mode.lower() == "openai":
        return OpenAIResearchProvider()
    return None
