from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


load_dotenv()


def normalize_azure_endpoint(endpoint: str) -> str:
    """Return the Azure OpenAI resource root expected by the SDK."""
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return endpoint.rstrip("/")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./agency.db"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str | None = None
    research_mode: str = "heuristic"
    ads_mode: str = "mock"
    ads_api_key: str | None = None
    ads_base_url: str = "https://api.ads.openai.com/v1"
    mutations_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", cls.openai_model),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT") or None,
            azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY") or None,
            azure_openai_api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", cls.azure_openai_api_version
            ),
            azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT") or None,
            research_mode=os.getenv("RESEARCH_MODE", cls.research_mode),
            ads_mode=os.getenv("ADS_MODE", cls.ads_mode),
            ads_api_key=os.getenv("OPENAI_ADS_API_KEY") or None,
            ads_base_url=os.getenv("OPENAI_ADS_BASE_URL", cls.ads_base_url),
            mutations_enabled=os.getenv("AGENCY_MUTATIONS_ENABLED", "false").lower()
            in {"1", "true", "yes"},
        )


settings = Settings.from_env()
