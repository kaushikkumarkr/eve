from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./agency.db"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
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
            research_mode=os.getenv("RESEARCH_MODE", cls.research_mode),
            ads_mode=os.getenv("ADS_MODE", cls.ads_mode),
            ads_api_key=os.getenv("OPENAI_ADS_API_KEY") or None,
            ads_base_url=os.getenv("OPENAI_ADS_BASE_URL", cls.ads_base_url),
            mutations_enabled=os.getenv("AGENCY_MUTATIONS_ENABLED", "false").lower()
            in {"1", "true", "yes"},
        )


settings = Settings.from_env()
