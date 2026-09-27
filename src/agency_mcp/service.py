"""Client profile, authorized evidence, and deterministic redaction services."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Client, Evidence, SourceDocument


FORBIDDEN_SOURCE_KINDS = {"synthetic", "simulation", "mock", "fixture", "test"}


def _audit(session: Session, action: str, entity_type: str, entity_id: str | None, client_id: str | None, payload: dict[str, Any], actor_id: str | None = None) -> None:
    # Centralize all audit writes on the actor-inclusive control-plane hash.
    from .control_plane import _audit as append_audit

    append_audit(
        session,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        client_id=client_id,
        actor_id=actor_id,
        payload=payload,
    )


def _require_client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    return client


def _sentences(content: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", content) if part.strip()]


def _redact_sensitive(content: str) -> tuple[str, dict[str, int]]:
    redactions: dict[str, int] = {}

    def replace(pattern: str, token: str, name: str, value: str) -> str:
        result, count = re.subn(pattern, token, value, flags=re.IGNORECASE)
        if count:
            redactions[name] = count
        return result

    content = replace(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", "email", content)
    content = replace(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)", "[REDACTED_PHONE]", "phone", content)
    content = replace(r"\b(?:\d[ -]*?){13,19}\b", "[REDACTED_PAYMENT]", "payment", content)
    return content, redactions


def create_client(session: Session, name: str, vertical: str = "general", website: str | None = None, profile: dict[str, Any] | None = None) -> Client:
    if not name.strip():
        raise ValueError("Client name is required")
    client = Client(name=name.strip(), vertical=vertical.strip() or "general", website=website, profile=profile or {})
    session.add(client)
    session.flush()
    _audit(session, "client.created", "client", client.id, client.id, {"name": client.name, "vertical": client.vertical})
    session.commit()
    return client


def update_client_profile(session: Session, client_id: str, profile: dict[str, Any]) -> Client:
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    client = _require_client(session, client_id)
    client.profile = {**(client.profile or {}), **profile}
    _audit(session, "client.profile.updated", "client", client.id, client.id, {"profile_keys": sorted(profile)})
    session.commit()
    return client


def ingest_text(
    session: Session,
    client_id: str,
    name: str,
    content: str,
    kind: str = "text",
    redact_sensitive: bool = True,
    source_url: str | None = None,
    retrieved_at: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    _require_client(session, client_id)
    if kind.lower() in FORBIDDEN_SOURCE_KINDS:
        raise ValueError("Synthetic or simulated source material is outside Eve's Ads-only evidence model")
    if not name.strip() or not content.strip():
        raise ValueError("Source name and content are required")
    stored_content, redactions = _redact_sensitive(content) if redact_sensitive else (content, {})
    source = SourceDocument(
        client_id=client_id,
        name=name.strip(),
        kind=kind.strip() or "text",
        content=stored_content,
        content_hash=hashlib.sha256(stored_content.encode("utf-8")).hexdigest(),
        redaction_summary=redactions,
        source_url=source_url,
        retrieved_at=retrieved_at,
        source_metadata=source_metadata or {},
    )
    session.add(source)
    session.flush()
    for index, statement in enumerate(_sentences(stored_content), start=1):
        session.add(
            Evidence(
                client_id=client_id,
                source_document_id=source.id,
                statement=statement,
                source_url=source_url,
                source_locator=f"sentence:{index}",
                evidence_type="source_content",
                review_status="unreviewed",
            )
        )
    session.flush()
    _audit(session, "source.ingested", "source_document", source.id, client_id, {"name": source.name, "kind": source.kind, "content_hash": source.content_hash, "redactions": redactions, "source_url": source_url, "retrieved_at": retrieved_at})
    session.commit()
    return source


def review_evidence(session: Session, evidence_id: str, review_status: str, reviewer: str, client_id: str | None = None) -> Evidence:
    if review_status not in {"approved", "rejected", "unreviewed"}:
        raise ValueError("review_status must be approved, rejected, or unreviewed")
    evidence = session.get(Evidence, evidence_id)
    if not evidence:
        raise ValueError(f"Unknown evidence: {evidence_id}")
    if client_id and evidence.client_id != client_id:
        raise ValueError("Evidence does not belong to the requested client")
    evidence.review_status = review_status
    _audit(session, "evidence.reviewed", "evidence", evidence.id, evidence.client_id, {"review_status": review_status}, reviewer)
    session.commit()
    return evidence
