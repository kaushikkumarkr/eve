"""Remote admin endpoints for secret metadata in shared deployments.

This is deliberately not an MCP tool. The masked CLI writes credential values
directly to Key Vault using an operator's set-only RBAC role. Eve receives only
the deterministic Key Vault locator; its control identity cannot read or write
client secret values.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp.server.auth.middleware.auth_context import get_access_token

from .config import settings
from .secret_store import (
    ALLOWED_SECRET_NAMES,
    list_client_secret_names,
    register_key_vault_secret_reference,
    unregister_key_vault_secret_reference,
)
from .db import session_scope

MAX_SECRET_INTAKE_BYTES = 16 * 1024


async def handle_secret_intake(request: Request) -> Response:
    token = get_access_token()
    if token is None:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if "admin" not in token.scopes:
        return JSONResponse({"error": "Admin role required"}, status_code=403)
    if settings.secret_backend != "azure_key_vault":
        return JSONResponse({"error": "Remote secret intake is not enabled"}, status_code=503)

    query = parse_qs(request.url.query)
    if request.method == "GET":
        client_id = (query.get("client_id") or [""])[0]
        if not client_id:
            return JSONResponse({"error": "client_id is required"}, status_code=400)
        with session_scope() as session:
            return JSONResponse({"client_id": client_id, "secrets": list_client_secret_names(session, client_id)})
    if request.method == "DELETE":
        client_id = (query.get("client_id") or [""])[0]
        name = (query.get("name") or [""])[0]
        if not client_id or name not in ALLOWED_SECRET_NAMES:
            return JSONResponse({"error": "Valid client_id and secret name are required"}, status_code=400)
        try:
            with session_scope() as session:
                deleted = unregister_key_vault_secret_reference(session, client_id, name, actor_id=token.client_id)
        except Exception:
            return JSONResponse({"error": "Secret metadata update failed; details withheld"}, status_code=500)
        return JSONResponse({"client_id": client_id, "name": name, "deleted": deleted, "upstream_key_revoked": False})
    if request.method != "POST":
        return JSONResponse({"error": "Method not allowed"}, status_code=405)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SECRET_INTAKE_BYTES:
                return JSONResponse({"error": "Request too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Invalid content length"}, status_code=400)

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_SECRET_INTAKE_BYTES:
            return JSONResponse({"error": "Request too large"}, status_code=413)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(payload, dict) or set(payload) != {"client_id", "name", "vault_secret_name"}:
        return JSONResponse({"error": "Expected client_id, name, and vault_secret_name"}, status_code=400)
    client_id, name, vault_secret_name = payload["client_id"], payload["name"], payload["vault_secret_name"]
    if not isinstance(client_id, str) or not client_id.strip():
        return JSONResponse({"error": "Invalid client_id"}, status_code=400)
    if name not in ALLOWED_SECRET_NAMES:
        return JSONResponse({"error": "Unsupported secret name"}, status_code=400)
    if not isinstance(vault_secret_name, str) or not vault_secret_name:
        return JSONResponse({"error": "Invalid Key Vault locator"}, status_code=400)

    try:
        with session_scope() as session:
            row = register_key_vault_secret_reference(
                session, client_id, name, vault_secret_name, actor_id=token.client_id
            )
        return JSONResponse({"client_id": client_id, "name": name, "stored": True, "secret_record_id": row.id})
    except Exception:
        return JSONResponse({"error": "Secret metadata update failed; details withheld"}, status_code=500)
