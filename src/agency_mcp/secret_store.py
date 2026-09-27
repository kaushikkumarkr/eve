from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Client, ClientSecret


def _audit_secret_event(
    session: Session,
    *,
    action: str,
    secret_id: str,
    client_id: str,
    name: str,
    actor_id: str | None,
    backend: str,
) -> None:
    # Import lazily to avoid a module cycle: the Ads adapter resolves this module
    # only when it needs a client-scoped secret.
    from .control_plane import _audit

    _audit(
        session,
        action=action,
        entity_type="client_secret",
        entity_id=secret_id,
        client_id=client_id,
        actor_id=actor_id,
        payload={"client_id": client_id, "name": name, "backend": backend},
    )


ALLOWED_SECRET_NAMES = {
    "OPENAI_ADS_API_KEY",
    "OPENAI_CONVERSIONS_API_KEY",
}


def generate_master_key() -> str:
    """Generate a Fernet key for the host environment, never for database storage."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    if not settings.agency_master_key:
        raise RuntimeError("AGENCY_MASTER_KEY is required for encrypted client secrets")
    try:
        return Fernet(settings.agency_master_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("AGENCY_MASTER_KEY is not a valid Fernet key") from exc


def _validate_name(name: str) -> None:
    if name not in ALLOWED_SECRET_NAMES:
        raise ValueError(f"Unsupported client secret name: {name}")


def _key_vault_secret_name(client_id: str, name: str) -> str:
    """Avoid putting a client name, raw ID, or secret category in the vault path."""
    digest = hashlib.sha256(f"{client_id}:{name}".encode("utf-8")).hexdigest()[:40]
    return f"eve-{digest}"


def register_key_vault_secret_reference(
    session: Session,
    client_id: str,
    name: str,
    vault_secret_name: str,
    actor_id: str | None = None,
) -> ClientSecret:
    """Persist only the deterministic locator after an admin CLI writes Key Vault directly."""
    _validate_name(name)
    if not session.get(Client, client_id):
        raise ValueError("Unknown client")
    expected_name = _key_vault_secret_name(client_id, name)
    if vault_secret_name != expected_name:
        raise ValueError("Key Vault locator does not match this client and credential")
    secret = session.scalar(
        select(ClientSecret).where(ClientSecret.client_id == client_id, ClientSecret.name == name)
    )
    if not secret:
        secret = ClientSecret(client_id=client_id, name=name, ciphertext=f"keyvault:{expected_name}")
        session.add(secret)
    else:
        secret.ciphertext = f"keyvault:{expected_name}"
    secret.updated_at = datetime.now(timezone.utc)
    session.flush()
    _audit_secret_event(
        session,
        action="client_secret.updated",
        secret_id=secret.id,
        client_id=client_id,
        name=name,
        actor_id=actor_id,
        backend="azure_key_vault",
    )
    session.commit()
    return secret


def unregister_key_vault_secret_reference(
    session: Session,
    client_id: str,
    name: str,
    actor_id: str | None = None,
) -> bool:
    """Remove DB metadata only; the operator CLI soft-deletes the vault secret directly."""
    _validate_name(name)
    if not session.get(Client, client_id):
        raise ValueError("Unknown client")
    secret = session.scalar(
        select(ClientSecret).where(ClientSecret.client_id == client_id, ClientSecret.name == name)
    )
    if not secret:
        return False
    if not secret.ciphertext.startswith("keyvault:"):
        raise RuntimeError("Credential is not stored in Azure Key Vault")
    _audit_secret_event(
        session,
        action="client_secret.locator_removed",
        secret_id=secret.id,
        client_id=client_id,
        name=name,
        actor_id=actor_id,
        backend="azure_key_vault",
    )
    session.delete(secret)
    session.commit()
    return True


def _key_vault_client():
    if not settings.azure_key_vault_url:
        raise RuntimeError("AZURE_KEY_VAULT_URL is required when EVE_SECRET_BACKEND=azure_key_vault")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:
        raise RuntimeError("Install the Azure optional dependencies with `uv sync --extra azure`") from exc
    return SecretClient(vault_url=settings.azure_key_vault_url, credential=DefaultAzureCredential())


def write_key_vault_client_secret(client_id: str, name: str, value: str) -> str:
    """Write a client secret directly from the admin CLI; return only its locator."""
    _validate_name(name)
    if settings.secret_backend != "azure_key_vault":
        raise RuntimeError("Direct Key Vault intake requires EVE_SECRET_BACKEND=azure_key_vault")
    if not value.strip():
        raise ValueError("Secret value cannot be empty")
    vault_name = _key_vault_secret_name(client_id, name)
    client = _key_vault_client()
    try:
        client.set_secret(vault_name, value)
    finally:
        client.close()
    return vault_name


def delete_key_vault_client_secret(client_id: str, name: str) -> bool:
    """Soft-delete a client secret directly from the admin CLI without reading it."""
    _validate_name(name)
    if settings.secret_backend != "azure_key_vault":
        raise RuntimeError("Direct Key Vault removal requires EVE_SECRET_BACKEND=azure_key_vault")
    client = _key_vault_client()
    try:
        client.begin_delete_secret(_key_vault_secret_name(client_id, name)).result()
    except Exception as exc:
        if type(exc).__name__ == "ResourceNotFoundError":
            return True
        raise
    finally:
        client.close()
    return True


def set_client_secret(
    session: Session,
    client_id: str,
    name: str,
    value: str,
    actor_id: str | None = None,
) -> ClientSecret:
    _validate_name(name)
    if not session.get(Client, client_id):
        raise ValueError(f"Unknown client: {client_id}")
    if not value.strip():
        raise ValueError("Secret value cannot be empty")
    if settings.secret_backend != "local_encrypted":
        raise RuntimeError("Shared Azure secrets must be written directly to Key Vault by the masked admin CLI")
    secret = session.scalar(
        select(ClientSecret).where(
            ClientSecret.client_id == client_id,
            ClientSecret.name == name,
        )
    )
    if not secret:
        secret = ClientSecret(client_id=client_id, name=name, ciphertext="")
        session.add(secret)
    secret.ciphertext = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    secret.updated_at = datetime.now(timezone.utc)
    session.flush()
    _audit_secret_event(
        session,
        action="client_secret.updated",
        secret_id=secret.id,
        client_id=secret.client_id,
        name=secret.name,
        actor_id=actor_id,
        backend=settings.secret_backend,
    )
    session.commit()
    return secret


def get_client_secret(session: Session, client_id: str, name: str) -> str | None:
    _validate_name(name)
    secret = session.scalar(
        select(ClientSecret).where(
            ClientSecret.client_id == client_id,
            ClientSecret.name == name,
        )
    )
    if not secret:
        return None
    if secret.ciphertext.startswith("keyvault:"):
        if settings.secret_backend != "azure_key_vault":
            raise RuntimeError("Client secret is stored in Key Vault; configure EVE_SECRET_BACKEND=azure_key_vault")
        return _key_vault_client().get_secret(secret.ciphertext.removeprefix("keyvault:")).value
    if settings.secret_backend != "local_encrypted":
        raise RuntimeError("Client secret backend does not match the stored credential locator")
    try:
        return _fernet().decrypt(secret.ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Client secret could not be decrypted; check AGENCY_MASTER_KEY") from exc


def delete_client_secret(session: Session, client_id: str, name: str, actor_id: str | None = None) -> bool:
    """Remove Eve's stored credential; Azure Key Vault deletion remains soft-deleted/recoverable."""
    _validate_name(name)
    if not session.get(Client, client_id):
        raise ValueError(f"Unknown client: {client_id}")
    secret = session.scalar(
        select(ClientSecret).where(
            ClientSecret.client_id == client_id,
            ClientSecret.name == name,
        )
    )
    if not secret:
        return False

    if secret.ciphertext.startswith("keyvault:"):
        if settings.secret_backend != "azure_key_vault":
            raise RuntimeError("Client secret is stored in Key Vault; configure EVE_SECRET_BACKEND=azure_key_vault")
        vault_name = secret.ciphertext.removeprefix("keyvault:")
        _key_vault_client().begin_delete_secret(vault_name).result()
        backend = "azure_key_vault_soft_delete"
    else:
        if settings.secret_backend != "local_encrypted":
            raise RuntimeError("Client secret backend does not match the stored credential locator")
        backend = "local_encrypted"

    _audit_secret_event(
        session,
        action="client_secret.deleted",
        secret_id=secret.id,
        client_id=secret.client_id,
        name=secret.name,
        actor_id=actor_id,
        backend=backend,
    )
    session.delete(secret)
    session.commit()
    return True


def list_client_secret_names(session: Session, client_id: str) -> list[dict[str, str]]:
    rows = session.scalars(
        select(ClientSecret)
        .where(ClientSecret.client_id == client_id)
        .order_by(ClientSecret.name)
    ).all()
    return [
        {"name": row.name, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}
        for row in rows
    ]
