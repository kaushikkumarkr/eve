from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ConversionEvent, ConversionSource

STANDARD_EVENTS = {
    "appointment_scheduled",
    "checkout_started",
    "contents_viewed",
    "items_added",
    "lead_created",
    "order_created",
    "page_viewed",
    "registration_completed",
    "subscription_created",
    "trial_started",
}


def register_conversion_source(
    session: Session,
    client_id: str,
    source_type: str,
    external_id: str,
    name: str,
) -> ConversionSource:
    source = ConversionSource(
        client_id=client_id,
        source_type=source_type,
        external_id=external_id,
        name=name,
    )
    session.add(source)
    session.commit()
    return source


def record_conversion(
    session: Session,
    client_id: str,
    source_id: str,
    event_id: str,
    event_type: str,
    value_minor: int | None = None,
    currency: str | None = None,
    action_source: str = "website",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = session.get(ConversionSource, source_id)
    if not source or source.client_id != client_id:
        raise ValueError("Conversion source does not belong to the client")
    if event_type not in STANDARD_EVENTS and event_type != "custom":
        raise ValueError(f"Unsupported conversion event: {event_type}")
    existing = session.scalar(
        select(ConversionEvent).where(
            ConversionEvent.source_id == source_id,
            ConversionEvent.event_id == event_id,
        )
    )
    if existing:
        return {"accepted": False, "deduplicated": True, "id": existing.id, "event_id": event_id}
    event = ConversionEvent(
        client_id=client_id,
        source_id=source_id,
        event_id=event_id,
        event_type=event_type,
        action_source=action_source,
        occurred_at=datetime.now(timezone.utc),
        value_minor=value_minor,
        currency=currency,
        payload=payload or {},
    )
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"accepted": False, "deduplicated": True, "event_id": event_id}
    return {"accepted": True, "deduplicated": False, "id": event.id, "event_id": event_id}
