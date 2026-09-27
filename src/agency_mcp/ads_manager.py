"""Safe, auditable handoff records for the official ChatGPT Ads Manager app.

This module does not automate or impersonate the official app. Operator-entered
actions and imported reporting data are explicitly labeled as such.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .control_plane import _audit
from .models import AdsExternalAction, AdsImportRecord, AdsInsightSnapshot, AdsManagerConnection, Client, utc_now


ALLOWED_ACTIONS = {"create", "edit", "pause", "activate", "archive", "other"}
ALLOWED_ENTITIES = {"campaign", "ad_group", "ad", "account", "other"}
ALLOWED_OUTCOMES = {"completed", "partially_completed", "failed", "not_attempted"}
ALLOWED_AGGREGATIONS = {"campaign", "ad_group", "ad"}
ALLOWED_METRICS = {
    "impressions", "clicks", "spend", "ctr", "cpc", "cpm", "cpa",
    "post_click_cvr", "conversions", "order_created_roas",
    "view_through_conversions", "click_through_conversions",
}


def _client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    return client


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_ads_manager_access(session: Session, client_id: str, account_id: str, actor_id: str, notes: str = "") -> AdsManagerConnection:
    _client(session, client_id)
    if not account_id.strip():
        raise ValueError("ad_account_id is required")
    connection = session.scalar(select(AdsManagerConnection).where(AdsManagerConnection.client_id == client_id))
    if connection is None:
        connection = AdsManagerConnection(client_id=client_id, ad_account_id=account_id.strip(), connected_by=actor_id)
        session.add(connection)
    else:
        connection.ad_account_id = account_id.strip()
        connection.status = "operator_attested"
        connection.connected_by = actor_id
        connection.access_confirmed_at = utc_now()
        connection.notes = notes[:1000]
    session.flush()
    _audit(session, action="ads_manager.connection_confirmed", entity_type="ads_manager_connection", entity_id=connection.id, client_id=client_id, actor_id=actor_id, payload={"ad_account_id": connection.ad_account_id, "status": connection.status, "provider": "official_chatgpt_ads_manager_operator_attestation"})
    session.commit()
    return connection


def record_external_action(
    session: Session, *, client_id: str, action_type: str, entity_type: str,
    entity_external_id: str | None, external_reference: str, outcome: str,
    details: dict[str, Any], evidence_reference: str | None,
    approved_payload_sha256: str | None, performed_at: datetime, actor_id: str,
) -> AdsExternalAction:
    connection = session.scalar(select(AdsManagerConnection).where(AdsManagerConnection.client_id == client_id))
    if not connection or connection.status != "operator_attested":
        raise ValueError("Record operator-attested Ads Manager account access before recording actions")
    if action_type not in ALLOWED_ACTIONS or entity_type not in ALLOWED_ENTITIES or outcome not in ALLOWED_OUTCOMES:
        raise ValueError("Unsupported action_type, entity_type, or outcome")
    if not external_reference.strip():
        raise ValueError("external_reference is required for idempotency")
    if session.scalar(select(AdsExternalAction.id).where(AdsExternalAction.client_id == client_id, AdsExternalAction.external_reference == external_reference.strip())):
        raise ValueError("external_reference has already been recorded for this client")
    if approved_payload_sha256 and (len(approved_payload_sha256) != 64 or any(c not in "0123456789abcdef" for c in approved_payload_sha256.lower())):
        raise ValueError("approved_payload_sha256 must be a SHA-256 hex digest")
    if performed_at.tzinfo is None:
        raise ValueError("performed_at must include a timezone")
    details_json = json.dumps(details, sort_keys=True, ensure_ascii=False)
    if len(details_json) > 20_000:
        raise ValueError("details exceed 20KB")
    if set(details) - {"platform_status", "platform_updated_fields", "reason_code"}:
        raise ValueError("details may contain only platform_status, platform_updated_fields, and reason_code")
    if any(not isinstance(v, (str, list)) for v in details.values()):
        raise ValueError("details values must be strings or lists of strings")
    if any(isinstance(v, str) and len(v) > 200 for v in details.values()):
        raise ValueError("details strings must be 200 characters or fewer")
    if "platform_updated_fields" in details and (not isinstance(details["platform_updated_fields"], list) or any(not isinstance(v, str) or len(v) > 80 for v in details["platform_updated_fields"])):
        raise ValueError("platform_updated_fields must be a short list of field names")
    item = AdsExternalAction(
        client_id=client_id, ad_account_id=connection.ad_account_id,
        action_type=action_type, entity_type=entity_type,
        entity_external_id=entity_external_id, external_reference=external_reference.strip(),
        outcome=outcome, details=details, evidence_reference=evidence_reference,
        approved_payload_sha256=approved_payload_sha256, actor_id=actor_id,
        performed_at=performed_at.astimezone(timezone.utc),
    )
    session.add(item)
    session.flush()
    _audit(session, action="ads_manager.external_action_recorded", entity_type="ads_external_action", entity_id=item.id, client_id=client_id, actor_id=actor_id, payload={"ad_account_id": item.ad_account_id, "action_type": action_type, "entity_type": entity_type, "external_reference": item.external_reference, "outcome": outcome, "approved_payload_sha256": approved_payload_sha256, "verification": "operator_reported_not_api_verified"})
    session.commit()
    return item


def import_ads_manager_snapshots(
    session: Session, *, client_id: str, account_id: str, source_name: str,
    snapshots: list[dict[str, Any]], actor_id: str, source_reference: str | None = None,
) -> dict[str, Any]:
    _client(session, client_id)
    connection = session.scalar(select(AdsManagerConnection).where(AdsManagerConnection.client_id == client_id))
    if not connection or connection.status != "operator_attested" or connection.ad_account_id != account_id.strip():
        raise ValueError("ad_account_id must match the operator-attested Ads Manager account")
    if not account_id.strip() or not source_name.strip():
        raise ValueError("ad_account_id and source_name are required")
    if not snapshots or len(snapshots) > 500:
        raise ValueError("Provide between 1 and 500 snapshots")
    normalized: list[dict[str, Any]] = []
    for row in snapshots:
        if set(row) - {"entity_external_id", "aggregation_level", "period", "timezone", "metrics", "freshness_state"}:
            raise ValueError("Snapshot contains unsupported fields")
        level = row.get("aggregation_level")
        if level not in ALLOWED_AGGREGATIONS:
            raise ValueError("aggregation_level must be campaign, ad_group, or ad")
        period = row.get("period")
        if not isinstance(period, str) or len(period) > 100 or not period.strip():
            raise ValueError("Each snapshot requires a reporting period")
        try:
            start_text, end_text = period.split("/", 1)
            if date.fromisoformat(start_text) > date.fromisoformat(end_text):
                raise ValueError("Reporting period start must be on or before its end")
        except (ValueError, TypeError) as exc:
            raise ValueError("period must use inclusive YYYY-MM-DD/YYYY-MM-DD format") from exc
        tz = row.get("timezone")
        if tz is not None:
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, TypeError) as exc:
                raise ValueError("timezone must be a valid IANA timezone") from exc
        metrics = row.get("metrics")
        if not isinstance(metrics, dict) or not metrics or set(metrics) - ALLOWED_METRICS:
            raise ValueError("metrics must contain supported Ads metrics only")
        for key, value in metrics.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"Metric {key} must be a finite number or null")
        freshness = row.get("freshness_state", "operator_reported")
        if freshness not in {"operator_reported", "unsettled", "settled", "unknown"}:
            raise ValueError("Unsupported freshness_state")
        normalized.append({
            "entity_external_id": str(row.get("entity_external_id") or "")[:300] or None,
            "aggregation_level": level, "period": period.strip(),
            "timezone": tz, "metrics": metrics,
            "freshness_state": freshness,
        })
    if source_reference is not None and len(source_reference) > 2048:
        raise ValueError("source_reference must be 2048 characters or fewer")
    content_hash = _hash({"ad_account_id": account_id.strip(), "source_name": source_name.strip(), "source_reference": source_reference, "snapshots": normalized})
    duplicate = session.scalar(select(AdsImportRecord).where(AdsImportRecord.client_id == client_id, AdsImportRecord.content_sha256 == content_hash))
    if duplicate:
        return {"import_id": duplicate.id, "duplicate": True, "snapshot_ids": duplicate.snapshot_ids, "content_sha256": content_hash}
    records = []
    for row in normalized:
        records.append(AdsInsightSnapshot(
            client_id=client_id, campaign_external_id=row["entity_external_id"],
            period=row["period"], metrics=row["metrics"],
            provider="chatgpt_ads_manager_operator_import",
            aggregation_level=row["aggregation_level"], timezone=row["timezone"],
            requested_fields=sorted(row["metrics"]), raw_response={},
            freshness_state=row["freshness_state"], api_version=None, fetched_at=utc_now(),
        ))
    session.add_all(records)
    session.flush()
    imported = AdsImportRecord(client_id=client_id, ad_account_id=account_id.strip(), content_sha256=content_hash, source_name=source_name.strip()[:300], source_reference=source_reference, imported_by=actor_id, snapshot_ids=[r.id for r in records])
    session.add(imported)
    session.flush()
    _audit(session, action="ads_manager.report_imported", entity_type="ads_import_record", entity_id=imported.id, client_id=client_id, actor_id=actor_id, payload={"ad_account_id": account_id.strip(), "source_name": imported.source_name, "source_reference": source_reference, "content_sha256": content_hash, "snapshot_count": len(records), "provider": "chatgpt_ads_manager_operator_import"})
    session.commit()
    return {"import_id": imported.id, "duplicate": False, "snapshot_ids": imported.snapshot_ids, "content_sha256": content_hash}
