from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
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


def validate_conversions_batch(pixel_id: str, events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not pixel_id.strip():
        errors.append("pixel_id is required")
    if not events:
        errors.append("events must not be empty")
    if len(events) > 1000:
        errors.append("events must contain at most 1000 items")
    for index, event in enumerate(events):
        for field in ("id", "type", "timestamp_ms", "action_source", "data"):
            if field not in event:
                errors.append(f"events[{index}].{field} is required")
        if "timestamp_ms" in event and not isinstance(event["timestamp_ms"], int):
            errors.append(f"events[{index}].timestamp_ms must be an integer")
    return errors


class ConversionsApiAdapter:
    """Small official Conversions API adapter with validation-only default."""

    def __init__(self, api_key: str, base_url: str = "https://bzr.openai.com/v1/events"):
        self.api_key = api_key
        self.base_url = base_url

    def send_events(
        self,
        pixel_id: str,
        events: list[dict[str, Any]],
        validate_only: bool = True,
        integration_source: str = "agency-mcp",
    ) -> dict[str, Any]:
        errors = validate_conversions_batch(pixel_id, events)
        if errors:
            raise ValueError("Invalid conversion batch: " + "; ".join(errors))
        payload = {
            "validate_only": validate_only,
            "integration_source": integration_source,
            "events": events,
        }
        request = Request(
            f"{self.base_url}?pid={quote(pixel_id, safe='')}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured official endpoint
            return json.loads(response.read().decode("utf-8"))


def check_conversion_batch(
    session: Session,
    client_id: str,
    pixel_id: str,
    events: list[dict[str, Any]],
    validate_only: bool = True,
) -> dict[str, Any]:
    errors = validate_conversions_batch(pixel_id, events)
    if errors:
        return {"valid": False, "errors": errors, "event_count": len(events)}
    from .secret_store import get_client_secret

    api_key = (
        get_client_secret(session, client_id, "OPENAI_CONVERSIONS_API_KEY")
        if settings.agency_master_key
        else None
    )
    if not api_key:
        return {
            "valid": True,
            "status": "locally_validated",
            "remote_check": "not_configured",
            "event_count": len(events),
            "validate_only": validate_only,
        }
    result = ConversionsApiAdapter(api_key).send_events(
        pixel_id,
        events,
        validate_only=validate_only,
    )
    return {
        "valid": True,
        "status": "remote_validated" if validate_only else "submitted",
        "event_count": len(events),
        "validate_only": validate_only,
        "remote_result": result,
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
