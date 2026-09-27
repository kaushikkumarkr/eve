"""Deterministic ChatGPT Ads control plane.

Codex and Claude Code decide *what to propose*. This module decides whether the
proposal is structurally safe, records it, and is the only path that can compile
an approved plan into an Advertiser API request. No function in this module asks
an LLM to construct an API payload.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ads import get_ads_adapter
from .models import (
    AdsInsightSnapshot,
    AdsWorkspace,
    AuditLog,
    CampaignBlueprint,
    CampaignLaunch,
    ChangeRequest,
    Client,
    ClientAccessGrant,
    ControlledExperiment,
    Evidence,
    HintSet,
    PolicyCheck,
    utc_now,
)
from .policy import check_advertising_policy


ALLOWED_BILLING_EVENTS = {"click", "impression"}
ALLOWED_BUDGET_TYPES = {"daily", "lifetime"}
ALLOWED_STRATEGIES = {"fixed_bid", "maximize_clicks", "maximize_conversions"}
SUPPORTED_BULK_STRATEGIES = {"fixed_bid"}
APPROVAL_TTL_DAYS = 7
DEFAULT_INSIGHT_FIELDS = [
    "impressions",
    "clicks",
    "spend",
    "ctr",
    "cpc",
    "cpm",
    "cpa",
    "post_click_cvr",
    "conversions",
    "order_created_roas",
]
INSIGHT_SETTLEMENT_HOURS = 72


def canonical_json(value: Any) -> str:
    """Canonical representation used for irreversible approval binding."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    return client


def grant_client_access(
    session: Session,
    client_id: str,
    operator_id: str,
    role: str,
    granted_by: str,
) -> ClientAccessGrant:
    """Create/update a per-client assignment; never grant database access."""
    _require_client(session, client_id)
    if role not in {"operator", "reviewer"}:
        raise ValueError("Client access role must be operator or reviewer")
    grant = session.scalar(
        select(ClientAccessGrant).where(
            ClientAccessGrant.client_id == client_id,
            ClientAccessGrant.operator_id == operator_id.strip(),
        )
    )
    if not grant:
        grant = ClientAccessGrant(client_id=client_id, operator_id=operator_id.strip(), role=role, granted_by=granted_by)
        session.add(grant)
    else:
        grant.role = role
        grant.granted_by = granted_by
    session.flush()
    _audit(
        session,
        action="client_access.granted",
        entity_type="client_access_grant",
        entity_id=grant.id,
        client_id=client_id,
        actor_id=granted_by,
        payload={"operator_id": grant.operator_id, "role": grant.role},
    )
    session.commit()
    return grant


def _audit(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    client_id: str | None,
    actor_id: str | None,
    payload: dict[str, Any],
) -> AuditLog:
    """Create an append-only, hash-linked audit entry without storing secrets."""
    previous = session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()))
    previous_hash = previous.entry_hash if previous else None
    hash_input = {
        "previous_hash": previous_hash,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "client_id": client_id,
        "actor_id": actor_id,
        "payload": payload,
    }
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        client_id=client_id,
        actor_id=actor_id,
        previous_hash=previous_hash,
        entry_hash=payload_sha256(hash_input),
        payload=payload,
    )
    session.add(entry)
    return entry


def verify_audit_chain(session: Session) -> dict[str, Any]:
    """Check the complete audit chain without returning sensitive event payloads."""
    entries = session.scalars(select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)).all()
    previous_expected_hash: str | None = None
    missing_hash_ids: list[str] = []
    invalid_hash_ids: list[str] = []
    broken_link_ids: list[str] = []
    legacy_hash_ids: list[str] = []

    for entry in entries:
        if not entry.entry_hash:
            missing_hash_ids.append(entry.id)
        if entry.previous_hash != previous_expected_hash:
            broken_link_ids.append(entry.id)
        expected = payload_sha256(
            {
                "previous_hash": previous_expected_hash,
                "action": entry.action,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "client_id": entry.client_id,
                "actor_id": entry.actor_id,
                "payload": entry.payload or {},
            }
        )
        if entry.entry_hash and entry.entry_hash != expected:
            legacy_expected = payload_sha256(
                {
                    "previous_hash": previous_expected_hash,
                    "action": entry.action,
                    "entity_type": entry.entity_type,
                    "entity_id": entry.entity_id,
                    "client_id": entry.client_id,
                    "payload": entry.payload or {},
                }
            )
            if entry.entry_hash == legacy_expected:
                # Historical source/evidence events omitted actor_id from their
                # digest. Integrity is checkable, but attribution was not bound.
                legacy_hash_ids.append(entry.id)
                previous_expected_hash = entry.entry_hash
            else:
                invalid_hash_ids.append(entry.id)
                previous_expected_hash = expected
        elif not entry.entry_hash:
            # Subsequent writers chain to the persisted value, which is None
            # for old rows created before hash fields existed.
            previous_expected_hash = None
        else:
            previous_expected_hash = expected

    if invalid_hash_ids or broken_link_ids:
        status = "tampered"
    elif missing_hash_ids or legacy_hash_ids:
        status = "incomplete"
    else:
        status = "verified"
    return {
        "status": status,
        "entry_count": len(entries),
        "missing_hash_count": len(missing_hash_ids),
        "invalid_hash_count": len(invalid_hash_ids),
        "broken_link_count": len(broken_link_ids),
        "legacy_hash_count": len(legacy_hash_ids),
        "first_invalid_entry_ids": (invalid_hash_ids + broken_link_ids + missing_hash_ids + legacy_hash_ids)[:20],
    }


def configure_ads_workspace(
    session: Session,
    client_id: str,
    *,
    ad_account_id: str | None = None,
    credential_name: str = "OPENAI_ADS_API_KEY",
    actor_id: str | None = None,
) -> AdsWorkspace:
    """Create/update non-secret account metadata. Credentials stay outside MCP."""
    _require_client(session, client_id)
    workspace = session.scalar(select(AdsWorkspace).where(AdsWorkspace.client_id == client_id))
    if not workspace:
        workspace = AdsWorkspace(client_id=client_id, credential_name=credential_name)
        session.add(workspace)
    if ad_account_id:
        workspace.ad_account_id = ad_account_id
    workspace.credential_name = credential_name
    workspace.status = "configured" if workspace.ad_account_id else "unconfigured"
    session.flush()
    _audit(
        session,
        action="ads_workspace.configured",
        entity_type="ads_workspace",
        entity_id=workspace.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={"ad_account_id": workspace.ad_account_id, "credential_name": credential_name},
    )
    session.commit()
    return workspace


def get_ads_workspace(session: Session, client_id: str) -> AdsWorkspace:
    workspace = session.scalar(select(AdsWorkspace).where(AdsWorkspace.client_id == client_id))
    if not workspace:
        raise ValueError("No Ads workspace exists; configure the client account metadata first")
    return workspace


def verify_ads_workspace(session: Session, client_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Read an account and mirror its observed state. This does not mutate Ads."""
    workspace = get_ads_workspace(session, client_id)
    account = get_ads_adapter(client_id, session).get_account()
    account_id = account.get("id")
    if workspace.ad_account_id and account_id and workspace.ad_account_id != account_id:
        raise ValueError("Configured ad_account_id does not match the credential's Ads account")
    workspace.ad_account_id = account_id or workspace.ad_account_id
    workspace.observed_state = account
    workspace.observed_at = utc_now()
    review = account.get("review") if isinstance(account.get("review"), dict) else {}
    workspace.status = "verified" if account.get("mode") == "mock" or account.get("status") else "configured"
    workspace.capabilities = account.get("capabilities", {}) if isinstance(account.get("capabilities"), dict) else {}
    session.flush()
    _audit(
        session,
        action="ads_workspace.verified",
        entity_type="ads_workspace",
        entity_id=workspace.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={
            "ad_account_id": workspace.ad_account_id,
            "account_status": account.get("status"),
            "review_status": review.get("status"),
            "provider": account.get("mode", "openai_ads"),
        },
    )
    session.commit()
    return {
        "workspace_id": workspace.id,
        "ad_account_id": workspace.ad_account_id,
        "status": workspace.status,
        "account": account,
        "credentials_exposed": False,
    }


def _hint_lint(hints: list[str]) -> dict[str, Any]:
    warnings: list[str] = []
    prohibited_patterns = {
        "guaranteed delivery": r"\bguarantee(?:d)?\b",
        "geographic targeting in hint": r"\b(?:near me|in [A-Z][a-z]+(?:,? [A-Z]{2})?)\b",
        "audience-list targeting in hint": r"\b(?:retarget|lookalike|audience list)\b",
    }
    for hint in hints:
        for label, pattern in prohibited_patterns.items():
            if re.search(pattern, hint, flags=re.IGNORECASE):
                warnings.append(f"{label}: {hint}")
        if len(hint) < 12:
            warnings.append(f"hint is likely too vague: {hint}")
    return {"valid": not any("guaranteed delivery" in item for item in warnings), "warnings": warnings}


def create_hint_set(
    session: Session,
    client_id: str,
    name: str,
    hints: list[str],
    *,
    evidence_ids: list[str] | None = None,
    rationale: str = "",
    actor_id: str | None = None,
) -> HintSet:
    _require_client(session, client_id)
    normalized = [item.strip() for item in hints if isinstance(item, str) and item.strip()]
    if not normalized:
        raise ValueError("At least one non-empty context hint is required")
    if len(normalized) > 2_000:
        raise ValueError("An ad group can contain at most 2,000 context hints")
    unique = list(dict.fromkeys(normalized))
    if len(unique) != len(normalized):
        raise ValueError("Context hints must not contain duplicates")
    evidence_ids = evidence_ids or []
    known_evidence = {
        item.id: item
        for item in session.scalars(
            select(Evidence).where(Evidence.client_id == client_id, Evidence.id.in_(evidence_ids))
        ).all()
    } if evidence_ids else {}
    unknown = sorted(set(evidence_ids) - set(known_evidence))
    if unknown:
        raise ValueError(f"Evidence IDs do not belong to this client: {unknown}")
    not_approved = sorted(
        evidence_id
        for evidence_id, item in known_evidence.items()
        if item.review_status != "approved"
    )
    if not_approved:
        raise ValueError(f"Only human-approved evidence may support a context-hint set: {not_approved}")
    last_version = session.scalar(
        select(HintSet.version)
        .where(HintSet.client_id == client_id, HintSet.name == name)
        .order_by(HintSet.version.desc())
    )
    lint = _hint_lint(unique)
    hint_set = HintSet(
        client_id=client_id,
        name=name.strip(),
        version=(last_version or 0) + 1,
        hints=unique,
        evidence_ids=evidence_ids,
        rationale=rationale.strip(),
        lint=lint,
        status="draft" if lint["valid"] else "blocked",
        created_by=actor_id,
    )
    session.add(hint_set)
    session.flush()
    _audit(
        session,
        action="hint_set.drafted",
        entity_type="hint_set",
        entity_id=hint_set.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={
            "name": hint_set.name,
            "version": hint_set.version,
            "hint_count": len(unique),
            "evidence_ids": evidence_ids,
            "lint_warning_count": len(lint["warnings"]),
        },
    )
    session.commit()
    return hint_set


def _blueprint_input(blueprint: CampaignBlueprint, hints: HintSet) -> dict[str, Any]:
    desired = blueprint.desired_state or {}
    campaign = dict(desired.get("campaign") or {})
    ad_group = dict(desired.get("ad_group") or {})
    ad = dict(desired.get("ad") or {})
    return {"campaign": campaign, "ad_group": ad_group, "ad": ad, "hints": list(hints.hints or [])}


def create_campaign_blueprint(
    session: Session,
    client_id: str,
    workspace_id: str,
    hint_set_id: str,
    name: str,
    desired_state: dict[str, Any],
    *,
    actor_id: str | None = None,
) -> CampaignBlueprint:
    _require_client(session, client_id)
    workspace = session.get(AdsWorkspace, workspace_id)
    hints = session.get(HintSet, hint_set_id)
    if not workspace or workspace.client_id != client_id:
        raise ValueError("Workspace does not belong to the requested client")
    if not hints or hints.client_id != client_id:
        raise ValueError("Hint set does not belong to the requested client")
    if hints.status == "blocked":
        raise ValueError("Hint set is blocked by deterministic linting")
    if not isinstance(desired_state, dict):
        raise ValueError("desired_state must be an object")
    blueprint = CampaignBlueprint(
        client_id=client_id,
        workspace_id=workspace_id,
        hint_set_id=hint_set_id,
        name=name.strip(),
        desired_state=desired_state,
        created_by=actor_id,
    )
    session.add(blueprint)
    session.flush()
    _audit(
        session,
        action="campaign_blueprint.drafted",
        entity_type="campaign_blueprint",
        entity_id=blueprint.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={"name": blueprint.name, "workspace_id": workspace_id, "hint_set_id": hint_set_id},
    )
    session.commit()
    return blueprint


def _validate_blueprint_input(
    client: Client,
    blueprint: CampaignBlueprint,
    hints: HintSet,
) -> tuple[list[str], list[str], dict[str, Any]]:
    payload = _blueprint_input(blueprint, hints)
    campaign, ad_group, ad = payload["campaign"], payload["ad_group"], payload["ad"]
    errors: list[str] = []
    warnings: list[str] = list((hints.lint or {}).get("warnings", []))

    if not campaign.get("name"):
        errors.append("campaign.name is required")
    elif not isinstance(campaign.get("name"), str) or not 3 <= len(campaign["name"]) <= 1_000:
        errors.append("campaign.name must be 3-1,000 characters")
    if campaign.get("billing_event_type") not in ALLOWED_BILLING_EVENTS:
        errors.append("campaign.billing_event_type must be click or impression for bulk P0")
    if campaign.get("budget_type") not in ALLOWED_BUDGET_TYPES:
        errors.append("campaign.budget_type must be daily or lifetime")
    if not isinstance(campaign.get("max_budget_micros"), int) or campaign.get("max_budget_micros", 0) < 1_000_000:
        errors.append("campaign.max_budget_micros must be at least 1,000,000 currency micros")
    countries = campaign.get("target_countries")
    if countries is not None and (
        not isinstance(countries, list)
        or not countries
        or not all(isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) for country in countries)
    ):
        errors.append("campaign.target_countries must be a non-empty list of ISO alpha-2 country codes when provided")

    if not ad_group.get("name"):
        errors.append("ad_group.name is required")
    elif not isinstance(ad_group.get("name"), str) or not 3 <= len(ad_group["name"]) <= 1_000:
        errors.append("ad_group.name must be 3-1,000 characters")
    strategy = ad_group.get("strategy", "fixed_bid")
    if strategy not in ALLOWED_STRATEGIES:
        errors.append(f"ad_group.strategy must be one of {sorted(ALLOWED_STRATEGIES)}")
    if strategy not in SUPPORTED_BULK_STRATEGIES:
        errors.append("P0 bulk compiler supports only fixed_bid; add a tested compiler path before using another strategy")
    # The Ads API uses max_bid_micros for both billing events. For impressions,
    # this is a per-impression amount (e.g. $60 CPM -> 60,000 micros/event).
    bid_field = "max_bid_micros"
    other_bid_field = "max_cpm_bid_micros"
    if strategy == "fixed_bid" and (
        not isinstance(ad_group.get(bid_field), int) or ad_group.get(bid_field, 0) <= 0
    ):
        errors.append(f"fixed_bid requires {bid_field} as a positive integer for either billing event")
    if ad_group.get(other_bid_field) is not None:
        errors.append(f"Ads API uses {bid_field} for click and impression bids; omit {other_bid_field}")
    if strategy != "fixed_bid" and (ad_group.get("max_bid_micros") is not None or ad_group.get("max_cpm_bid_micros") is not None):
        errors.append("Maximize Results strategies must omit fixed bid fields")

    title = ad.get("title", "")
    body = ad.get("body", "")
    target_url = ad.get("target_url", "")
    image_url = ad.get("source_image_url", "")
    if not ad.get("name"):
        errors.append("ad.name is required")
    if not isinstance(title, str) or not 3 <= len(title) <= 50:
        errors.append("ad.title must be 3-50 characters")
    if not isinstance(body, str) or len(body) > 100:
        errors.append("ad.body must be at most 100 characters")
    if not isinstance(target_url, str) or len(target_url) > 2_048 or not re.match(r"^https?://", target_url):
        errors.append("ad.target_url must be an HTTP(S) URL no longer than 2,048 characters")
    if not isinstance(image_url, str) or not re.match(r"^https?://", image_url):
        errors.append("ad.source_image_url must be a publicly accessible HTTP(S) image URL for bulk creation")
    if not payload["hints"]:
        errors.append("blueprint needs at least one hint in its hint set")

    policy = check_advertising_policy(client.vertical, title if isinstance(title, str) else "", body if isinstance(body, str) else "", target_url if isinstance(target_url, str) else "")
    if policy.status == "rejected":
        errors.extend(policy.reasons)
    elif policy.status == "manual_review":
        warnings.extend(policy.reasons)
    return errors, warnings, {"status": policy.status, "reasons": policy.reasons, "policy_version": policy.policy_version}


def _stable_key(blueprint: CampaignBlueprint, suffix: str, content_hash: str) -> str:
    return f"eve-{blueprint.id[:8]}-v{blueprint.version}-{suffix}-{content_hash[:12]}"


def compile_blueprint(session: Session, blueprint_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Compile the exact, paused bulk request from a persisted blueprint.

    The documented bulk schema differs from single-resource endpoints. Keep this
    compiler deliberately narrow and fail rather than guessing an undocumented
    field shape.
    """
    blueprint = session.get(CampaignBlueprint, blueprint_id)
    if not blueprint:
        raise ValueError(f"Unknown campaign blueprint: {blueprint_id}")
    client = _require_client(session, blueprint.client_id)
    hints = session.get(HintSet, blueprint.hint_set_id)
    if not hints:
        raise ValueError("Blueprint hint set no longer exists")
    errors, warnings, policy = _validate_blueprint_input(client, blueprint, hints)
    session.add(
        PolicyCheck(
            client_id=client.id,
            entity_type="campaign_blueprint",
            entity_id=blueprint.id,
            status=policy["status"],
            policy_version=policy["policy_version"],
            reasons=policy["reasons"],
        )
    )
    validation = {"valid": not errors, "errors": errors, "warnings": warnings, "policy": policy}
    blueprint.validation = validation
    if errors:
        blueprint.status = "blocked"
        session.flush()
        _audit(
            session,
            action="campaign_blueprint.blocked",
            entity_type="campaign_blueprint",
            entity_id=blueprint.id,
            client_id=client.id,
            actor_id=actor_id,
            payload={"errors": errors, "warning_count": len(warnings)},
        )
        session.commit()
        return {"blueprint_id": blueprint.id, "payload": None, **validation}

    desired = _blueprint_input(blueprint, hints)
    intent_hash = payload_sha256({"blueprint_id": blueprint.id, "version": blueprint.version, "desired": desired})
    campaign_key = _stable_key(blueprint, "campaign", intent_hash)
    ad_group_key = _stable_key(blueprint, "adgroup", intent_hash)
    ad_key = _stable_key(blueprint, "ad", intent_hash)
    campaign_input = {
        "name": desired["campaign"]["name"],
        "max_budget_micros": desired["campaign"]["max_budget_micros"],
        "billing_event_type": desired["campaign"]["billing_event_type"],
        "budget_type": desired["campaign"]["budget_type"],
        "status": "paused",
    }
    if desired["campaign"].get("target_countries"):
        campaign_input["target_countries"] = desired["campaign"]["target_countries"]
    ad_group_input: dict[str, Any] = {
        "campaign_idempotency_key": campaign_key,
        "name": desired["ad_group"]["name"],
        "status": "paused",
        "context_hints": desired["hints"],
    }
    if desired["ad_group"].get("max_bid_micros") is not None:
        ad_group_input["max_bid_micros"] = desired["ad_group"]["max_bid_micros"]
    payload = {
        "validate_only": True,
        "partial_failure": False,
        "operations": [
            {
                "operation_id": "create-campaign",
                "type": "campaign.create",
                "idempotency_key": campaign_key,
                "input": campaign_input,
            },
            {
                "operation_id": "create-ad-group",
                "type": "ad_group.create",
                "idempotency_key": ad_group_key,
                "input": ad_group_input,
            },
            {
                "operation_id": "create-ad",
                "type": "ad.create",
                "idempotency_key": ad_key,
                "input": {
                    "campaign_idempotency_key": campaign_key,
                    "ad_group_idempotency_key": ad_group_key,
                    "name": desired["ad"]["name"],
                    "title": desired["ad"]["title"],
                    "body": desired["ad"]["body"],
                    "target_url": desired["ad"]["target_url"],
                    "source_image_url": desired["ad"]["source_image_url"],
                    "status": "paused",
                },
            },
        ],
    }
    digest = payload_sha256(payload)
    blueprint.compiled_payload = payload
    blueprint.compiled_sha256 = digest
    blueprint.status = "locally_valid"
    session.flush()
    _audit(
        session,
        action="campaign_blueprint.compiled",
        entity_type="campaign_blueprint",
        entity_id=blueprint.id,
        client_id=client.id,
        actor_id=actor_id,
        payload={"payload_sha256": digest, "operation_count": len(payload["operations"]), "warning_count": len(warnings)},
    )
    session.commit()
    return {"blueprint_id": blueprint.id, "payload": payload, "payload_sha256": digest, **validation}


def propose_build_change(
    session: Session,
    blueprint_id: str,
    rationale: str,
    actor_id: str | None = None,
) -> ChangeRequest:
    compiled = compile_blueprint(session, blueprint_id, actor_id)
    if not compiled["valid"] or not compiled["payload"]:
        raise ValueError("Blueprint must pass local validation before a change can be proposed")
    blueprint = session.get(CampaignBlueprint, blueprint_id)
    assert blueprint is not None
    request = ChangeRequest(
        client_id=blueprint.client_id,
        blueprint_id=blueprint.id,
        action="build_paused",
        status="proposed",
        payload=compiled["payload"],
        payload_sha256=compiled["payload_sha256"],
        rationale=rationale.strip(),
        expires_at=utc_now() + timedelta(days=APPROVAL_TTL_DAYS),
    )
    session.add(request)
    blueprint.status = "change_proposed"
    session.flush()
    _audit(
        session,
        action="change.proposed",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={"action": request.action, "payload_sha256": request.payload_sha256, "blueprint_id": blueprint.id},
    )
    session.commit()
    return request


def validate_change_with_platform(session: Session, change_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Submit an asynchronous validate_only job; polling decides its final outcome."""
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.action != "build_paused":
        raise ValueError("Only paused build requests can use bulk validation")
    payload = dict(request.payload)
    if payload.get("validate_only") is not True:
        raise ValueError("Stored change is not a validation-safe bulk payload")
    result = get_ads_adapter(request.client_id, session).validate_bulk_job(payload)
    bulk_job = result.get("job") if isinstance(result.get("job"), dict) else {}
    bulk_job_id = bulk_job.get("id")
    if not bulk_job_id:
        raise ValueError("OpenAI Ads validation response did not contain a bulk job id")
    request.result = {
        **(request.result or {}),
        "platform_validation": {
            "submission": result,
            "bulk_job_id": bulk_job_id,
            "status": bulk_job.get("status", "submitted"),
        },
    }
    request.status = "platform_validation_pending"
    session.flush()
    _audit(
        session,
        action="change.platform_validation_submitted",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={"bulk_job_id": bulk_job_id, "provider": result.get("mode", "openai_ads")},
    )
    session.commit()
    return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "result": result, "pending": True}


def poll_change_platform_validation(session: Session, change_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Poll a submitted bulk validation and preserve every operation result before accepting it."""
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.status != "platform_validation_pending":
        raise ValueError("Only a pending platform validation can be polled")
    validation = (request.result or {}).get("platform_validation")
    if not isinstance(validation, dict) or not isinstance(validation.get("bulk_job_id"), str):
        raise ValueError("Pending validation has no recorded bulk job id")
    bulk_job_id = validation["bulk_job_id"]
    adapter = get_ads_adapter(request.client_id, session)
    job = adapter.get_bulk_job(bulk_job_id)
    job_status = job.get("status")
    if job_status not in {"completed", "partially_failed", "failed"}:
        request.result = {**(request.result or {}), "platform_validation": {**validation, "status": job_status, "last_job": job}}
        session.commit()
        return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "job": job, "pending": True}

    operations: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = adapter.get_bulk_operations(bulk_job_id, after=after)
        page_data = page.get("data", [])
        if not isinstance(page_data, list):
            raise ValueError("Bulk operation result page has an invalid data field")
        operations.extend(item for item in page_data if isinstance(item, dict))
        if not page.get("has_more"):
            break
        after = page.get("last_operation_id") or page.get("last_id")
        if not after and page_data:
            after = page_data[-1].get("operation_id") if isinstance(page_data[-1], dict) else None
        if not isinstance(after, str) or not after:
            raise ValueError("Bulk operation pagination response omitted its cursor")
    expected_ids = {
        operation.get("operation_id")
        for operation in request.payload.get("operations", [])
        if isinstance(operation, dict)
    }
    returned_ids = [item.get("operation_id") for item in operations]
    expected_operation_count = len(expected_ids)
    failed = [item for item in operations if item.get("status") in {"failed", "skipped"}]
    unexpected = [
        item for item in operations
        if item.get("status") != "validated" or item.get("operation_id") not in expected_ids
    ]
    complete = (
        len(operations) == expected_operation_count
        and len(set(returned_ids)) == expected_operation_count
        and set(returned_ids) == expected_ids
    )
    validated = job_status == "completed" and complete and not failed and not unexpected
    request.result = {
        **(request.result or {}),
        "platform_validation": {
            **validation,
            "status": job_status,
            "last_job": job,
            "operations": operations,
            "failure_count": len(failed),
            "unexpected_status_count": len(unexpected),
            "expected_operation_count": expected_operation_count,
        },
    }
    request.status = "platform_validated" if validated else "platform_validation_failed"
    _audit(
        session,
        action="change.platform_validation_completed",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={
            "bulk_job_id": bulk_job_id,
            "job_status": job_status,
            "failure_count": len(failed),
            "unexpected_status_count": len(unexpected),
            "complete": complete,
        },
    )
    session.commit()
    return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "job": job, "operations": operations, "pending": False}


def record_client_approval(
    session: Session,
    change_id: str,
    approval_reference: str,
    approved_by: str,
    actor_id: str | None = None,
) -> ChangeRequest:
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.status != "platform_validated":
        raise ValueError("A complete successful validate_only result is required before client approval")
    if not approval_reference.strip() or not approved_by.strip():
        raise ValueError("Client approval needs a written approval reference and named approver")
    request.client_approval_reference = approval_reference.strip()
    request.client_approved_by = approved_by.strip()
    request.client_approved_at = utc_now()
    request.status = "client_approved"
    _audit(
        session,
        action="change.client_approved",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={"payload_sha256": request.payload_sha256, "approval_reference": request.client_approval_reference},
    )
    session.commit()
    return request


def admin_approve_change(
    session: Session,
    change_id: str,
    payload_hash: str,
    admin_id: str,
) -> ChangeRequest:
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.status != "client_approved":
        raise ValueError("A recorded client approval is required before admin approval")
    validation = (request.result or {}).get("platform_validation")
    if not isinstance(validation, dict) or validation.get("status") != "completed":
        raise ValueError("A terminal successful platform validation is required before admin approval")
    if request.expires_at and request.expires_at <= utc_now():
        request.status = "expired"
        session.commit()
        raise ValueError("Change approval window has expired; propose a fresh change")
    if payload_hash != request.payload_sha256:
        raise ValueError("Admin approval hash does not match the immutable change payload")
    request.admin_approved_by = admin_id.strip()
    request.admin_approved_at = utc_now()
    request.status = "admin_approved"
    _audit(
        session,
        action="change.admin_approved",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=admin_id,
        payload={"payload_sha256": request.payload_sha256},
    )
    session.commit()
    return request


def apply_approved_paused_change(session: Session, change_id: str, actor_id: str) -> dict[str, Any]:
    """Submit a pre-approved paused bulk build; polling decides whether it was built."""
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.status != "admin_approved":
        raise ValueError("Only a hash-bound admin-approved change may be applied")
    validation = (request.result or {}).get("platform_validation")
    if not isinstance(validation, dict) or validation.get("status") != "completed":
        raise ValueError("A terminal successful platform validation is required before applying a paused build")
    if request.expires_at and request.expires_at <= utc_now():
        request.status = "expired"
        session.commit()
        raise ValueError("Change approval window has expired")
    if payload_sha256(request.payload) != request.payload_sha256:
        raise ValueError("Stored change payload integrity check failed")
    for operation in request.payload.get("operations", []):
        if operation.get("input", {}).get("status") != "paused":
            raise ValueError("Eve only builds paused resources")
    result = get_ads_adapter(request.client_id, session).apply_paused_bulk_job(request.payload, actor_id)
    bulk_job = result.get("job") if isinstance(result.get("job"), dict) else {}
    bulk_job_id = bulk_job.get("id")
    if not bulk_job_id:
        raise ValueError("OpenAI Ads build response did not contain a bulk job id")
    request.result = {
        **(request.result or {}),
        "apply": {"submission": result, "bulk_job_id": bulk_job_id, "status": bulk_job.get("status", "submitted")},
    }
    request.status = "build_pending"
    _audit(
        session,
        action="change.paused_build_submitted",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={"payload_sha256": request.payload_sha256, "bulk_job_id": bulk_job_id},
    )
    session.commit()
    return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "result": result, "pending": True}


def poll_paused_build(session: Session, change_id: str, actor_id: str) -> dict[str, Any]:
    """Record final bulk apply outcomes and mark a build complete only after every create succeeds."""
    request = session.get(ChangeRequest, change_id)
    if not request:
        raise ValueError(f"Unknown change request: {change_id}")
    if request.status != "build_pending":
        raise ValueError("Only a pending paused build can be polled")
    apply = (request.result or {}).get("apply")
    if not isinstance(apply, dict) or not isinstance(apply.get("bulk_job_id"), str):
        raise ValueError("Pending build has no recorded bulk job id")
    bulk_job_id = apply["bulk_job_id"]
    adapter = get_ads_adapter(request.client_id, session)
    job = adapter.get_bulk_job(bulk_job_id)
    job_status = job.get("status")
    if job_status not in {"completed", "partially_failed", "failed"}:
        request.result = {**(request.result or {}), "apply": {**apply, "status": job_status, "last_job": job}}
        session.commit()
        return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "job": job, "pending": True}

    operations: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = adapter.get_bulk_operations(bulk_job_id, after=after)
        page_data = page.get("data", [])
        if not isinstance(page_data, list):
            raise ValueError("Bulk operation result page has an invalid data field")
        operations.extend(item for item in page_data if isinstance(item, dict))
        if not page.get("has_more"):
            break
        after = page.get("last_operation_id") or page.get("last_id")
        if not after and page_data:
            after = page_data[-1].get("operation_id") if isinstance(page_data[-1], dict) else None
        if not isinstance(after, str) or not after:
            raise ValueError("Bulk operation pagination response omitted its cursor")
    expected_ids = {
        operation.get("operation_id")
        for operation in request.payload.get("operations", [])
        if isinstance(operation, dict)
    }
    returned_ids = [item.get("operation_id") for item in operations]
    expected_operation_count = len(expected_ids)
    failed = [item for item in operations if item.get("status") in {"failed", "skipped"}]
    unexpected = [
        item for item in operations
        if item.get("status") != "created" or item.get("operation_id") not in expected_ids
    ]
    complete = (
        len(operations) == expected_operation_count
        and len(set(returned_ids)) == expected_operation_count
        and set(returned_ids) == expected_ids
    )
    built = job_status == "completed" and complete and not failed and not unexpected
    request.result = {
        **(request.result or {}),
        "apply": {
            **apply,
            "status": job_status,
            "last_job": job,
            "operations": operations,
            "failure_count": len(failed),
            "unexpected_status_count": len(unexpected),
            "expected_operation_count": expected_operation_count,
        },
    }
    request.applied_at = utc_now() if built else None
    request.status = "built_paused" if built else "apply_failed"
    blueprint = session.get(CampaignBlueprint, request.blueprint_id) if request.blueprint_id else None
    if blueprint and built:
        blueprint.status = "built_paused"
    _audit(
        session,
        action="change.paused_build_completed",
        entity_type="change_request",
        entity_id=request.id,
        client_id=request.client_id,
        actor_id=actor_id,
        payload={
            "payload_sha256": request.payload_sha256,
            "bulk_job_id": bulk_job_id,
            "job_status": job_status,
            "failure_count": len(failed),
            "unexpected_status_count": len(unexpected),
            "complete": complete,
        },
    )
    session.commit()
    return {"change_id": request.id, "status": request.status, "bulk_job_id": bulk_job_id, "job": job, "operations": operations, "pending": False}


def sync_entity_insights(
    session: Session,
    client_id: str,
    entity_external_id: str,
    *,
    aggregation_level: str,
    period: str,
    timezone: str | None = None,
    requested_fields: list[str] | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    workspace = get_ads_workspace(session, client_id)
    if aggregation_level not in {"campaign", "ad_group", "ad"}:
        raise ValueError("aggregation_level must be campaign, ad_group, or ad")
    if not isinstance(entity_external_id, str) or not entity_external_id.strip():
        raise ValueError("entity_external_id is required")
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", period):
        raise ValueError("period must use inclusive YYYY-MM-DD/YYYY-MM-DD dates")
    since, until = period.split("/", 1)
    if since > until:
        raise ValueError("period start must be on or before its end")
    account_timezone = timezone or (workspace.observed_state or {}).get("timezone")
    if not isinstance(account_timezone, str) or not account_timezone.strip():
        raise ValueError("timezone is required until the Ads account has been verified")
    fields = requested_fields or DEFAULT_INSIGHT_FIELDS
    if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field.strip() for field in fields):
        raise ValueError("requested_fields must be a non-empty list of field names")
    request_params = {
        "aggregation_level": aggregation_level,
        "time_granularity": "none",
        "time_ranges[]": [json.dumps({"type": "date_range", "since": since, "until": until, "timezone": account_timezone})],
        "fields[]": fields,
    }
    result = get_ads_adapter(client_id, session).get_insights(aggregation_level, entity_external_id, request_params)
    provider = result.get("provider", result.get("mode", "openai_ads"))
    if provider == "mock":
        freshness = "mock"
    else:
        try:
            local_zone = ZoneInfo(account_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        period_end = datetime.combine(
            datetime.strptime(until, "%Y-%m-%d").date() + timedelta(days=1),
            time.min,
            tzinfo=local_zone,
        )
        settle_after = period_end.astimezone(timezone.utc) + timedelta(hours=INSIGHT_SETTLEMENT_HOURS)
        freshness = "provisionally_settled" if datetime.now(timezone.utc) >= settle_after else "unsettled"
    rows = result.get("data") if isinstance(result.get("data"), list) else []
    metrics: dict[str, Any] = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {"rows": rows, "count": result.get("count")}
    snapshot = AdsInsightSnapshot(
        client_id=client_id,
        # Retain the original DB column for migration compatibility; aggregation_level
        # makes the entity type explicit for campaign, ad-group, and ad snapshots.
        campaign_external_id=entity_external_id,
        period=period,
        metrics=metrics,
        provider=provider,
        aggregation_level=aggregation_level,
        timezone=account_timezone,
        requested_fields=fields,
        raw_response=result,
        freshness_state=freshness,
        api_version="v1",
    )
    session.add(snapshot)
    session.flush()
    _audit(
        session,
        action="insights.snapshot_created",
        entity_type="ads_insight_snapshot",
        entity_id=snapshot.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={"entity_external_id": entity_external_id, "aggregation_level": aggregation_level, "period": period, "provider": provider, "freshness_state": freshness},
    )
    session.commit()
    return {"snapshot_id": snapshot.id, "metrics": snapshot.metrics, "provider": provider, "freshness_state": freshness}


def sync_campaign_insights(
    session: Session,
    client_id: str,
    campaign_external_id: str,
    *,
    period: str,
    timezone: str | None = None,
    requested_fields: list[str] | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible campaign snapshot wrapper."""
    return sync_entity_insights(
        session,
        client_id,
        campaign_external_id,
        aggregation_level="campaign",
        period=period,
        timezone=timezone,
        requested_fields=requested_fields,
        actor_id=actor_id,
    )


def create_controlled_experiment(
    session: Session,
    client_id: str,
    *,
    name: str,
    hypothesis: str,
    changed_variable: str,
    primary_metric: str,
    guardrails: dict[str, Any],
    decision_rule: str,
    arms: list[dict[str, Any]],
    attribution: dict[str, Any],
    timeframe: dict[str, Any],
    actor_id: str | None = None,
) -> ControlledExperiment:
    _require_client(session, client_id)
    if len(arms) != 2:
        raise ValueError("A controlled comparison needs exactly two documented arms")
    arm_names = [arm.get("name") for arm in arms if isinstance(arm, dict)]
    if len(arm_names) != 2 or any(not isinstance(name, str) or not name.strip() for name in arm_names) or len(set(arm_names)) != 2:
        raise ValueError("Each comparison arm needs a unique, non-empty name")
    for arm in arms:
        if arm.get("aggregation_level") != "ad_group":
            raise ValueError("Current hint-cluster comparisons require ad_group aggregation_level for each arm")
        if not isinstance(arm.get("entity_external_id"), str) or not arm["entity_external_id"].strip():
            raise ValueError("Each comparison arm needs its platform entity_external_id")
    required = {"name": name, "hypothesis": hypothesis, "changed_variable": changed_variable, "primary_metric": primary_metric, "decision_rule": decision_rule}
    missing = [field for field, value in required.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        raise ValueError(f"Experiment fields are required: {missing}")
    if not isinstance(timeframe, dict) or not timeframe.get("start") or not timeframe.get("decision_date") or not timeframe.get("timezone"):
        raise ValueError("Experiment timeframe needs start, decision_date, and account timezone")
    try:
        start_date = datetime.strptime(timeframe["start"], "%Y-%m-%d").date()
        decision_date = datetime.strptime(timeframe["decision_date"], "%Y-%m-%d").date()
        ZoneInfo(timeframe["timezone"])
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Experiment timeframe dates must be YYYY-MM-DD and timezone must be a valid IANA timezone") from exc
    if start_date >= decision_date:
        raise ValueError("Experiment decision_date must be after its start date")
    experiment = ControlledExperiment(
        client_id=client_id,
        name=name.strip(),
        hypothesis=hypothesis.strip(),
        changed_variable=changed_variable.strip(),
        primary_metric=primary_metric.strip(),
        guardrails=guardrails,
        decision_rule=decision_rule.strip(),
        arms=arms,
        attribution=attribution,
        timeframe=timeframe,
        status="pre_registered",
    )
    session.add(experiment)
    session.flush()
    _audit(
        session,
        action="experiment.pre_registered",
        entity_type="controlled_experiment",
        entity_id=experiment.id,
        client_id=client_id,
        actor_id=actor_id,
        payload={"primary_metric": experiment.primary_metric, "changed_variable": experiment.changed_variable, "arm_count": 2},
    )
    session.commit()
    return experiment


def evaluate_controlled_experiment(
    session: Session,
    experiment_id: str,
    snapshot_ids: dict[str, str],
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Attach and assess matched snapshots without making or recording a decision."""
    experiment = session.get(ControlledExperiment, experiment_id)
    if not experiment:
        raise ValueError(f"Unknown controlled experiment: {experiment_id}")
    if experiment.status not in {"pre_registered", "assessed"}:
        raise ValueError("Only a pre-registered experiment can be assessed; final decisions are terminal")
    arm_names = {arm["name"] for arm in experiment.arms}
    if not isinstance(snapshot_ids, dict) or set(snapshot_ids) != arm_names:
        raise ValueError("Provide exactly one snapshot ID for each registered arm name")

    snapshots: dict[str, AdsInsightSnapshot] = {}
    for arm in experiment.arms:
        name = arm["name"]
        snapshot = session.get(AdsInsightSnapshot, snapshot_ids[name])
        if not snapshot or snapshot.client_id != experiment.client_id:
            raise ValueError(f"Snapshot for arm {name!r} is missing or outside this client")
        if snapshot.aggregation_level != arm["aggregation_level"]:
            raise ValueError(f"Snapshot for arm {name!r} has the wrong aggregation level")
        if snapshot.campaign_external_id != arm["entity_external_id"]:
            raise ValueError(f"Snapshot for arm {name!r} belongs to a different Ads entity")
        snapshots[name] = snapshot

    periods = {snapshot.period for snapshot in snapshots.values()}
    timezones = {snapshot.timezone for snapshot in snapshots.values()}
    providers = {snapshot.provider for snapshot in snapshots.values()}
    aggregation_levels = {snapshot.aggregation_level for snapshot in snapshots.values()}
    if len(periods) != 1 or len(timezones) != 1 or len(providers) != 1 or len(aggregation_levels) != 1:
        raise ValueError("Comparison snapshots must have the same period, timezone, provider, and aggregation level")
    expected_period = f"{experiment.timeframe['start']}/{experiment.timeframe['decision_date']}"
    if next(iter(periods)) != expected_period:
        raise ValueError("Comparison snapshots must cover the exact pre-registered start/decision-date window")
    if next(iter(timezones)) != experiment.timeframe["timezone"]:
        raise ValueError("Comparison snapshots must use the pre-registered account timezone")

    metric_name = experiment.primary_metric
    metric_values: dict[str, int | float | None] = {}
    for name, snapshot in snapshots.items():
        metrics = snapshot.metrics or {}
        if isinstance(metrics.get("rows"), list):
            raise ValueError("Comparison requires one total-period row per arm; daily/multi-row results are not auto-aggregated")
        value = metrics.get(metric_name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"Primary metric {metric_name!r} is not numeric for arm {name!r}")
        metric_values[name] = value

    provider = next(iter(providers))
    freshness_values = {snapshot.freshness_state for snapshot in snapshots.values()}
    data_quality = "comparable"
    quality_warnings: list[str] = []
    if provider == "mock":
        data_quality = "mock_ineligible"
        quality_warnings.append("Mock data cannot support a client performance decision.")
    elif provider != "openai_ads":
        data_quality = "provider_unverified"
        quality_warnings.append("Only OpenAI Ads API snapshots can support a client performance decision.")
    elif freshness_values not in ({"settled"}, {"provisionally_settled"}):
        data_quality = "unsettled"
        quality_warnings.append("Metrics have not passed Eve's conservative 72-hour processing buffer.")
    elif any(value is None for value in metric_values.values()):
        data_quality = "metric_unavailable"
        quality_warnings.append("The pre-registered primary metric is null or unavailable for at least one arm.")
    result = {
        "kind": "directional_observation_not_randomized_test",
        "data_quality": data_quality,
        "period": next(iter(periods)),
        "timezone": next(iter(timezones)),
        "provider": provider,
        "aggregation_level": next(iter(aggregation_levels)),
        "primary_metric": metric_name,
        "arm_observations": [
            {
                "name": arm["name"],
                "entity_external_id": arm["entity_external_id"],
                "snapshot_id": snapshots[arm["name"]].id,
                "value": metric_values[arm["name"]],
                "freshness_state": snapshots[arm["name"]].freshness_state,
            }
            for arm in experiment.arms
        ],
        "quality_warnings": quality_warnings,
        "assessment": {"recorded_by": actor_id, "recorded_at": utc_now().isoformat(), "decision_made": False},
    }
    experiment.result = result
    experiment.status = "assessed"
    _audit(
        session,
        action="experiment.snapshots_assessed",
        entity_type="controlled_experiment",
        entity_id=experiment.id,
        client_id=experiment.client_id,
        actor_id=actor_id,
        payload={
            "data_quality": data_quality,
            "snapshot_ids": [snapshot_ids[name] for name in sorted(snapshot_ids)],
            "decision_made": False,
        },
    )
    session.commit()
    return {"id": experiment.id, "status": experiment.status, "result": result}


def record_controlled_experiment_decision(
    session: Session,
    experiment_id: str,
    decision: str,
    approval_reference: str,
    rationale: str,
    actor_id: str,
) -> ControlledExperiment:
    """Record the separately approved human decision after snapshot assessment."""
    experiment = session.get(ControlledExperiment, experiment_id)
    if not experiment:
        raise ValueError(f"Unknown controlled experiment: {experiment_id}")
    if experiment.status != "assessed" or not experiment.result:
        raise ValueError("Assess and persist the comparison snapshots before recording a decision")
    if decision not in {"scale", "iterate", "pause", "inconclusive"}:
        raise ValueError("decision must be scale, iterate, pause, or inconclusive")
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        raise ValueError("A client or agency human-approval reference is required")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("A written human rationale is required")
    data_quality = experiment.result.get("data_quality")
    if data_quality != "comparable" and decision != "inconclusive":
        raise ValueError(f"Only an inconclusive decision may be recorded with data quality {data_quality}")
    result = {
        **experiment.result,
        "decision": {
            "type": decision,
            "rationale": rationale.strip(),
            "approval_reference": approval_reference.strip(),
            "recorded_by": actor_id,
            "recorded_at": utc_now().isoformat(),
            "causal_claim": False,
        },
    }
    experiment.result = result
    experiment.status = decision
    _audit(
        session,
        action="experiment.human_decision_recorded",
        entity_type="controlled_experiment",
        entity_id=experiment.id,
        client_id=experiment.client_id,
        actor_id=actor_id,
        payload={
            "decision": decision,
            "approval_reference": approval_reference.strip(),
            "data_quality": data_quality,
            "causal_claim": False,
        },
    )
    session.commit()
    return experiment


def workspace_summary(session: Session, client_id: str) -> dict[str, Any]:
    workspace = get_ads_workspace(session, client_id)
    blueprints = session.scalars(select(CampaignBlueprint).where(CampaignBlueprint.client_id == client_id)).all()
    changes = session.scalars(select(ChangeRequest).where(ChangeRequest.client_id == client_id).order_by(ChangeRequest.created_at.desc())).all()
    hint_sets = session.scalars(select(HintSet).where(HintSet.client_id == client_id)).all()
    snapshots = session.scalars(select(AdsInsightSnapshot).where(AdsInsightSnapshot.client_id == client_id)).all()
    experiments = session.scalars(select(ControlledExperiment).where(ControlledExperiment.client_id == client_id)).all()
    return {
        "workspace": {
            "id": workspace.id,
            "ad_account_id": workspace.ad_account_id,
            "status": workspace.status,
            "observed_at": workspace.observed_at.isoformat() if workspace.observed_at else None,
            "credential_name": workspace.credential_name,
            "credentials_exposed": False,
        },
        "counts": {
            "hint_sets": len(hint_sets),
            "blueprints": len(blueprints),
            "changes": len(changes),
            "insight_snapshots": len(snapshots),
            "controlled_experiments": len(experiments),
        },
        "recent_changes": [
            {"id": change.id, "action": change.action, "status": change.status, "payload_sha256": change.payload_sha256}
            for change in changes[:10]
        ],
        "controlled_comparisons": [
            {"id": item.id, "name": item.name, "status": item.status, "primary_metric": item.primary_metric, "arms": item.arms, "result": item.result}
            for item in experiments
        ],
    }
