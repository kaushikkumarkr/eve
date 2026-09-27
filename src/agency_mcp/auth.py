from __future__ import annotations

import asyncio
import hmac
from urllib.parse import urlsplit

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .config import settings
from .models import ClientAccessGrant


def entra_operator_scope(resource_url: str) -> str:
    """Use the fully qualified delegated scope on the MCP resource's exact URI."""
    return f"{resource_url.rstrip('/')}/operator"


def mcp_operator_scope(resource_url: str, auth_mode: str) -> str:
    """Return the scope string configured in the active identity provider."""
    return "operator" if auth_mode == "auth0" else entra_operator_scope(resource_url)


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
            )
        if settings.mcp_operator_token and hmac.compare_digest(token, settings.mcp_operator_token):
            return AccessToken(
                token=token,
                client_id="agency-operator",
                scopes=["operator"],
                resource=settings.mcp_public_url,
            )
        return None


class EntraTokenVerifier:
    """Validate single-tenant Entra access tokens and derive per-person roles."""

    def __init__(self, tenant_id: str, audience: str, resource: str):
        self.tenant_id = tenant_id
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.resource = resource
        self._jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_jwk_set=True,
            lifespan=3600,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await asyncio.to_thread(self._verify, token)
        except Exception:
            # Key rotation/network failures and invalid claims all fail closed.
            return None

    def _verify(self, token: str) -> AccessToken | None:
        signing_key = self._jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "tid", "oid"]},
        )
        if claims.get("tid") != self.tenant_id:
            return None
        roles = claims.get("roles", [])
        if not isinstance(roles, list):
            return None
        operator_scope = entra_operator_scope(self.resource)
        delegated_scopes = claims.get("scp", "").split()
        if operator_scope not in delegated_scopes:
            return None
        if "Eve.Admin" in roles:
            scopes = [operator_scope, "admin"]
        elif "Eve.Operator" in roles:
            scopes = [operator_scope]
        else:
            return None
        object_id = claims.get("oid")
        if not isinstance(object_id, str) or not object_id:
            return None
        return AccessToken(
            token=token,
            client_id=f"{self.tenant_id}:{object_id}",
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.resource,
        )


class Auth0TokenVerifier:
    """Validate Auth0-issued MCP access tokens and map explicit Eve roles."""

    def __init__(self, domain: str, audience: str, resource: str, role_claim: str):
        normalized_domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not normalized_domain or "/" in normalized_domain:
            raise RuntimeError("AUTH0_DOMAIN must be a tenant host, without a path")
        self.issuer = f"https://{normalized_domain}/"
        self.audience = audience
        self.resource = resource
        self.role_claim = role_claim
        self._jwks = jwt.PyJWKClient(
            f"{self.issuer}.well-known/jwks.json",
            cache_jwk_set=True,
            lifespan=3600,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await asyncio.to_thread(self._verify, token)
        except Exception:
            # Fail closed on invalid claims, network failures, and key rotation errors.
            return None

    def _verify(self, token: str) -> AccessToken | None:
        signing_key = self._jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        raw_scopes = claims.get("scope", "")
        if not isinstance(raw_scopes, str):
            return None
        scope_values = set(raw_scopes.split())
        raw_permissions = claims.get("permissions", [])
        if not isinstance(raw_permissions, list) or not all(
            isinstance(item, str) for item in raw_permissions
        ):
            return None
        operator_scope = "operator"
        if not ({operator_scope, entra_operator_scope(self.resource)} & (scope_values | set(raw_permissions))):
            return None
        roles = claims.get(self.role_claim, [])
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            return None
        if "Eve.Admin" in roles:
            scopes = [operator_scope, "admin"]
        elif "Eve.Operator" in roles:
            scopes = [operator_scope]
        else:
            return None
        return AccessToken(
            token=token,
            client_id=f"auth0:{subject}",
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.resource,
        )


def build_http_auth() -> tuple[AuthSettings, TokenVerifier]:
    if settings.mcp_auth_mode == "entra":
        if not settings.entra_tenant_id or not settings.mcp_audience:
            raise RuntimeError(
                "AZURE_TENANT_ID and AGENCY_MCP_AUDIENCE are required for Entra HTTP authentication"
            )
        if not settings.mcp_public_url.startswith("https://"):
            raise RuntimeError("Entra-authenticated MCP must use an HTTPS public resource URL")
        tenant_issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
        operator_scope = entra_operator_scope(settings.mcp_public_url)
        auth = AuthSettings(
            issuer_url=tenant_issuer,
            resource_server_url=settings.mcp_public_url,
            required_scopes=[operator_scope],
            validate_token_resource=False,
        )
        return auth, EntraTokenVerifier(
            settings.entra_tenant_id,
            settings.mcp_audience,
            settings.mcp_public_url,
        )
    if settings.mcp_auth_mode == "auth0":
        if not settings.auth0_domain or not settings.auth0_audience:
            raise RuntimeError("AUTH0_DOMAIN and AUTH0_AUDIENCE are required for Auth0 HTTP authentication")
        if not settings.mcp_public_url.startswith("https://"):
            raise RuntimeError("Auth0-authenticated MCP must use an HTTPS public resource URL")
        if settings.auth0_audience != settings.mcp_public_url:
            raise RuntimeError("AUTH0_AUDIENCE must exactly match AGENCY_MCP_PUBLIC_URL")
        verifier = Auth0TokenVerifier(
            settings.auth0_domain,
            settings.auth0_audience,
            settings.mcp_public_url,
            settings.auth0_role_claim,
        )
        auth = AuthSettings(
            issuer_url=verifier.issuer,
            resource_server_url=settings.mcp_public_url,
            required_scopes=["operator"],
            validate_token_resource=False,
        )
        return auth, verifier
    if settings.mcp_auth_mode != "static":
        raise RuntimeError("AGENCY_MCP_AUTH_MODE must be static, entra, or auth0")
    if not settings.mcp_admin_token or not settings.mcp_operator_token:
        raise RuntimeError(
            "AGENCY_ADMIN_TOKEN and AGENCY_OPERATOR_TOKEN are required for streamable-http mode"
        )
    if len(settings.mcp_admin_token) < 32 or len(settings.mcp_operator_token) < 32:
        raise RuntimeError("HTTP bootstrap tokens must each contain at least 32 characters")
    if hmac.compare_digest(settings.mcp_admin_token, settings.mcp_operator_token):
        raise RuntimeError("Admin and operator HTTP tokens must be different")
    parsed_url = urlsplit(settings.mcp_public_url)
    if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("AGENCY_MCP_PUBLIC_URL must use HTTPS except for loopback development")
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


def current_actor_id() -> str:
    """Return the authenticated subject without relying on an LLM-supplied name."""
    token = get_access_token()
    if token:
        return token.client_id
    return settings.agency_operator_id


def require_admin() -> None:
    if current_role() != "admin":
        raise PermissionError("Admin role is required for approvals and Ads mutations")


def require_client_access(session: Session, client_id: str) -> None:
    """Require explicit client membership for operators; admins retain break-glass access."""
    if current_role() == "admin":
        return
    grant = session.scalar(
        select(ClientAccessGrant).where(
            ClientAccessGrant.client_id == client_id,
            ClientAccessGrant.operator_id == current_actor_id(),
        )
    )
    if not grant:
        raise PermissionError("This operator is not assigned to the requested client workspace")


def visible_client_ids(session: Session) -> set[str] | None:
    """None means all clients (admin); operators receive only their explicit grants."""
    if current_role() == "admin":
        return None
    return set(
        session.scalars(
            select(ClientAccessGrant.client_id).where(ClientAccessGrant.operator_id == current_actor_id())
        ).all()
    )
