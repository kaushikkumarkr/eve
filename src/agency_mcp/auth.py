from __future__ import annotations

import hmac

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .config import settings


class StaticTokenVerifier:
    """Small two-role verifier for a private internal deployment.

    This is intentionally not an identity provider. For a larger team, replace
    it with Cloudflare Access/OAuth and keep the MCP server behind that boundary.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if settings.mcp_admin_token and hmac.compare_digest(token, settings.mcp_admin_token):
            return AccessToken(
                token=token,
                client_id="agency-admin",
                scopes=["operator", "admin"],
                resource=settings.mcp_public_url,
                claims={"role": "admin"},
            )
        if settings.mcp_operator_token and hmac.compare_digest(token, settings.mcp_operator_token):
            return AccessToken(
                token=token,
                client_id="agency-operator",
                scopes=["operator"],
                resource=settings.mcp_public_url,
                claims={"role": "operator"},
            )
        return None


def build_http_auth() -> tuple[AuthSettings, TokenVerifier]:
    if not settings.mcp_admin_token or not settings.mcp_operator_token:
        raise RuntimeError(
            "AGENCY_ADMIN_TOKEN and AGENCY_OPERATOR_TOKEN are required for streamable-http mode"
        )
    auth = AuthSettings(
        issuer_url=settings.mcp_public_url,
        resource_server_url=settings.mcp_public_url,
        required_scopes=["operator"],
        validate_token_resource=True,
    )
    return auth, StaticTokenVerifier()


def current_role() -> str:
    token = get_access_token()
    if token:
        return "admin" if "admin" in token.scopes else "operator"
    return settings.agency_role


def require_admin() -> None:
    if current_role() != "admin":
        raise PermissionError("Admin role is required for approvals and Ads mutations")
