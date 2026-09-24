from __future__ import annotations

from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import AuditLog, Client, ClientSecret


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


def set_client_secret(session: Session, client_id: str, name: str, value: str) -> ClientSecret:
    _validate_name(name)
    if not session.get(Client, client_id):
        raise ValueError(f"Unknown client: {client_id}")
    if not value.strip():
        raise ValueError("Secret value cannot be empty")
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
    session.add(
        AuditLog(
            action="client_secret.updated",
            entity_type="client_secret",
            entity_id=secret.id,
            payload={"client_id": client_id, "name": name},
        )
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
    try:
        return _fernet().decrypt(secret.ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Client secret could not be decrypted; check AGENCY_MASTER_KEY") from exc


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
