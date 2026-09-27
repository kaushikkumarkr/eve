from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace

from starlette.requests import Request
from starlette.testclient import TestClient
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

import agency_mcp.secret_intake as intake
import agency_mcp.cli as cli
import agency_mcp.mcp_server as mcp_server
from typer.testing import CliRunner


def make_request(body: bytes = b"{}") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/admin/client-secrets",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            "server": ("eve.internal", 443),
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )


def test_remote_secret_intake_requires_authenticated_admin(monkeypatch):
    monkeypatch.setattr(intake, "get_access_token", lambda: None)
    response = asyncio.run(intake.handle_secret_intake(make_request()))
    assert response.status_code == 401

    monkeypatch.setattr(
        intake,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["operator"], client_id="tenant:operator"),
    )
    response = asyncio.run(intake.handle_secret_intake(make_request()))
    assert response.status_code == 403


def test_remote_admin_route_persists_only_deterministic_locator(monkeypatch):
    stored = {}
    monkeypatch.setattr(intake, "settings", SimpleNamespace(secret_backend="azure_key_vault"))
    monkeypatch.setattr(
        intake,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["operator", "admin"], client_id="tenant:admin"),
    )
    monkeypatch.setattr(intake, "session_scope", lambda: _SessionContext())

    def fake_register(session, client_id, name, vault_secret_name, actor_id):
        stored.update(client_id=client_id, name=name, vault_secret_name=vault_secret_name, actor_id=actor_id)
        return SimpleNamespace(id="secret-row-id")

    monkeypatch.setattr(intake, "register_key_vault_secret_reference", fake_register)
    body = json.dumps(
        {
            "client_id": "client-123",
            "name": "OPENAI_ADS_API_KEY",
            "vault_secret_name": "eve-locator-only",
        }
    ).encode()
    response = asyncio.run(intake.handle_secret_intake(make_request(body)))

    assert response.status_code == 200
    assert stored == {
        "client_id": "client-123",
        "name": "OPENAI_ADS_API_KEY",
        "vault_secret_name": "eve-locator-only",
        "actor_id": "tenant:admin",
    }
    assert json.loads(response.body) == {
        "client_id": "client-123",
        "name": "OPENAI_ADS_API_KEY",
        "stored": True,
        "secret_record_id": "secret-row-id",
    }


def test_secret_metadata_route_rejects_any_secret_value_field(monkeypatch):
    monkeypatch.setattr(intake, "settings", SimpleNamespace(secret_backend="azure_key_vault"))
    monkeypatch.setattr(
        intake,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["operator", "admin"], client_id="tenant:admin"),
    )
    called = []
    monkeypatch.setattr(intake, "register_key_vault_secret_reference", lambda *args, **kwargs: called.append(args))
    body = json.dumps(
        {
            "client_id": "client-123",
            "name": "OPENAI_ADS_API_KEY",
            "vault_secret_name": "locator-only",
            "value": "sensitive-test-string",
        }
    ).encode()
    response = asyncio.run(intake.handle_secret_intake(make_request(body)))
    assert response.status_code == 400
    assert b"sensitive-test-string" not in response.body
    assert not called


def test_shared_secret_cli_sends_only_key_vault_locator_to_eve(monkeypatch):
    secret_value = "test-only-not-a-real-key"
    captured = {}
    monkeypatch.setattr(
        cli,
        "settings",
        type(cli.settings)(
            **{
                **cli.settings.__dict__,
                "secret_backend": "azure_key_vault",
                "secret_intake_url": "https://eve.private/admin/client-secrets",
                "secret_intake_scope": "api://eve/.default",
            }
        ),
    )
    monkeypatch.setattr(cli, "getpass", lambda _prompt: secret_value)
    monkeypatch.setattr(cli, "write_key_vault_client_secret", lambda _client_id, _name, value: "eve-deterministic-locator")

    def fake_remote_secret_request(**kwargs):
        captured.update(kwargs)
        return {"client_id": "client-123", "name": "OPENAI_ADS_API_KEY", "stored": True}

    monkeypatch.setattr(cli, "remote_secret_request", fake_remote_secret_request)
    result = CliRunner().invoke(cli.app, ["secret-set", "client-123", "OPENAI_ADS_API_KEY"])

    assert result.exit_code == 0, result.output
    assert secret_value not in result.output
    assert captured["payload"] == {
        "client_id": "client-123",
        "name": "OPENAI_ADS_API_KEY",
        "vault_secret_name": "eve-deterministic-locator",
    }
    assert "value" not in captured["payload"]


def test_registered_private_secret_route_enforces_authentication_and_admin_role(monkeypatch):
    class TestVerifier:
        async def verify_token(self, token):
            if token not in {"admin-token", "operator-token"}:
                return None
            scopes = ["operator", "admin"] if token == "admin-token" else ["operator"]
            return AccessToken(token=token, client_id=f"test:{token}", scopes=scopes)

    mcp = mcp_server.mcp
    old_auth = mcp.settings.auth
    old_verifier = mcp._token_verifier
    mcp.settings.auth = AuthSettings(
        issuer_url="https://issuer.example/",
        resource_server_url="https://eve.example/mcp",
        required_scopes=["operator"],
        validate_token_resource=False,
    )
    mcp._token_verifier = TestVerifier()
    monkeypatch.setattr(intake, "settings", SimpleNamespace(secret_backend="azure_key_vault"))
    monkeypatch.setattr(intake, "session_scope", lambda: _SessionContext())
    monkeypatch.setattr(
        intake,
        "register_key_vault_secret_reference",
        lambda *_args, **_kwargs: SimpleNamespace(id="metadata-row"),
    )
    body = {
        "client_id": "client-123",
        "name": "OPENAI_ADS_API_KEY",
        "vault_secret_name": "locator-only",
    }
    try:
        with TestClient(mcp.streamable_http_app()) as client:
            missing = client.post("/admin/client-secrets", json=body)
            operator = client.post(
                "/admin/client-secrets", json=body, headers={"Authorization": "Bearer operator-token"}
            )
            admin = client.post(
                "/admin/client-secrets", json=body, headers={"Authorization": "Bearer admin-token"}
            )
        assert missing.status_code == 401
        assert operator.status_code == 403
        assert admin.status_code == 200
        assert admin.json()["stored"] is True
        assert "value" not in admin.json()
    finally:
        mcp.settings.auth = old_auth
        mcp._token_verifier = old_verifier


class _SessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback):
        return False
