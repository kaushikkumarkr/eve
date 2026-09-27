from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()

@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./agency.db"
    ads_mode: str = "mock"
    ads_base_url: str = "https://api.ads.openai.com/v1"
    mutations_enabled: bool = False
    agency_role: str = "admin"
    agency_operator_id: str = "local-admin"
    process_role: str = "control"
    agency_master_key: str | None = None
    secret_backend: str = "local_encrypted"
    azure_key_vault_url: str | None = None
    mcp_transport: str = "stdio"
    mcp_auth_mode: str = "static"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_public_url: str = "http://127.0.0.1:8000/mcp"
    mcp_json_response: bool = False
    mcp_stateless_http: bool = False
    mcp_admin_token: str | None = None
    mcp_operator_token: str | None = None
    entra_tenant_id: str | None = None
    mcp_audience: str | None = None
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    auth0_role_claim: str = "https://eve.internal/roles"
    secret_intake_url: str | None = None
    secret_intake_scope: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            ads_mode=os.getenv("ADS_MODE", cls.ads_mode),
            ads_base_url=os.getenv("OPENAI_ADS_BASE_URL", cls.ads_base_url),
            mutations_enabled=os.getenv("AGENCY_MUTATIONS_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            agency_role=os.getenv("AGENCY_ROLE", cls.agency_role).lower(),
            agency_operator_id=os.getenv("AGENCY_OPERATOR_ID", cls.agency_operator_id),
            process_role=os.getenv("EVE_PROCESS_ROLE", cls.process_role).lower(),
            agency_master_key=os.getenv("AGENCY_MASTER_KEY") or None,
            secret_backend=os.getenv("EVE_SECRET_BACKEND", cls.secret_backend).lower(),
            azure_key_vault_url=os.getenv("AZURE_KEY_VAULT_URL") or None,
            mcp_transport=os.getenv("AGENCY_MCP_TRANSPORT", cls.mcp_transport).lower(),
            mcp_auth_mode=os.getenv("AGENCY_MCP_AUTH_MODE", cls.mcp_auth_mode).lower(),
            mcp_host=os.getenv("AGENCY_MCP_HOST", cls.mcp_host),
            mcp_port=int(os.getenv("AGENCY_MCP_PORT", str(cls.mcp_port))),
            mcp_public_url=os.getenv("AGENCY_MCP_PUBLIC_URL", cls.mcp_public_url),
            mcp_json_response=os.getenv("AGENCY_MCP_JSON_RESPONSE", "false").lower()
            in {"1", "true", "yes"},
            mcp_stateless_http=os.getenv("AGENCY_MCP_STATELESS_HTTP", "false").lower()
            in {"1", "true", "yes"},
            mcp_admin_token=os.getenv("AGENCY_ADMIN_TOKEN") or None,
            mcp_operator_token=os.getenv("AGENCY_OPERATOR_TOKEN") or None,
            entra_tenant_id=os.getenv("AZURE_TENANT_ID") or None,
            mcp_audience=os.getenv("AGENCY_MCP_AUDIENCE") or None,
            auth0_domain=os.getenv("AUTH0_DOMAIN") or None,
            auth0_audience=os.getenv("AUTH0_AUDIENCE") or None,
            auth0_role_claim=os.getenv("AUTH0_ROLE_CLAIM", cls.auth0_role_claim),
            secret_intake_url=os.getenv("AGENCY_SECRET_INTAKE_URL") or None,
            secret_intake_scope=os.getenv("AGENCY_SECRET_INTAKE_SCOPE") or None,
        )


settings = Settings.from_env()
