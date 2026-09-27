"""Narrow OpenAI Advertiser API adapter.

The adapter intentionally implements only account reads, insights reads, bulk
`validate_only`, and hash-approved paused bulk builds. It is not a generic HTTP
client and does not expose activation, audiences, feeds, or conversion sending.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import settings


class AdsMutationBlocked(RuntimeError):
    """Raised whenever an unapproved or disabled Ads mutation is attempted."""


class AdsAdapter(Protocol):
    def get_account(self) -> dict[str, Any]: ...
    def get_campaign(self, campaign_id: str) -> dict[str, Any]: ...
    def get_ad_group(self, ad_group_id: str) -> dict[str, Any]: ...
    def get_ad(self, ad_id: str) -> dict[str, Any]: ...
    def activate_campaign(self, campaign_id: str) -> dict[str, Any]: ...
    def activate_ad_group(self, ad_group_id: str) -> dict[str, Any]: ...
    def activate_ad(self, ad_id: str) -> dict[str, Any]: ...
    def pause_campaign(self, campaign_id: str) -> dict[str, Any]: ...
    def pause_account(self) -> dict[str, Any]: ...
    def get_insights(self, aggregation_level: str, entity_id: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def validate_bulk_job(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_bulk_job(self, job_id: str) -> dict[str, Any]: ...
    def get_bulk_operations(self, job_id: str, after: str | None = None) -> dict[str, Any]: ...
    def apply_paused_bulk_job(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]: ...


@dataclass
class MockAdsAdapter:
    account_id: str = "mock_account"

    def get_account(self) -> dict[str, Any]:
        return {
            "id": self.account_id,
            "name": "Mock test advertiser",
            "status": "active",
            "review": {"status": "approved"},
            "currency_code": "USD",
            "timezone": "America/New_York",
            "capabilities": {"bulk_api": False, "pixel_management": False, "conversion_bidding": False},
            "mode": "mock",
            "spend": 0,
            "account_integrity_review": {"status": "approved"},
        }

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return {
            "id": campaign_id,
            "status": "paused",
            "budget": {"daily_spend_limit_micros": 1_000_000},
            "serving_issues": [],
            "mode": "mock",
        }

    def get_ad_group(self, ad_group_id: str) -> dict[str, Any]:
        return {
            "id": ad_group_id,
            "campaign_id": "cmpn_mock",
            "status": "paused",
            "serving_issues": [],
            "mode": "mock",
        }

    def get_ad(self, ad_id: str) -> dict[str, Any]:
        return {
            "id": ad_id,
            "ad_group_id": "adgrp_mock",
            "status": "paused",
            "review_status": "approved",
            "review": {"status": "approved"},
            "serving_issues": [],
            "mode": "mock",
        }

    def activate_campaign(self, campaign_id: str) -> dict[str, Any]:
        return {"id": campaign_id, "status": "active", "mode": "mock", "spend": 0}

    def activate_ad_group(self, ad_group_id: str) -> dict[str, Any]:
        return {"id": ad_group_id, "status": "active", "mode": "mock", "spend": 0}

    def activate_ad(self, ad_id: str) -> dict[str, Any]:
        return {"id": ad_id, "status": "active", "review_status": "approved", "mode": "mock", "spend": 0}

    def pause_campaign(self, campaign_id: str) -> dict[str, Any]:
        return {"id": campaign_id, "status": "paused", "mode": "mock", "spend": 0}

    def pause_account(self) -> dict[str, Any]:
        return {"id": self.account_id, "status": "paused", "mode": "mock", "spend": 0}

    def get_insights(self, aggregation_level: str, entity_id: str, params: dict[str, Any]) -> dict[str, Any]:
        fields = params.get("fields[]") or []
        id_field = {"campaign": "campaign_id", "ad_group": "ad_group_id", "ad": "ad_id"}.get(aggregation_level)
        if not id_field:
            raise ValueError("Insights aggregation level must be campaign, ad_group, or ad")
        row = {
            "impressions": 0,
            "clicks": 0,
            "spend": 0.0,
            "ctr": 0.0,
            "cpc": None,
            "conversions": 0,
        }
        if fields:
            row = {key: row.get(key) for key in fields if key in row}
        row[id_field] = entity_id
        return {"object": "list", "data": [row], "count": 1, "provider": "mock", "request": params}

    def validate_bulk_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"accepted": True, "mode": "mock", "dry_run": True, "job": {"id": "mock-bulk-validation", "status": "completed", "payload": payload}, "spend": 0}

    def get_bulk_job(self, job_id: str) -> dict[str, Any]:
        return {"id": job_id, "status": "completed", "mode": "mock"}

    def get_bulk_operations(self, job_id: str, after: str | None = None) -> dict[str, Any]:
        outcome = "created" if job_id == "mock-bulk-apply" else "validated"
        return {
            "data": [
                {"operation_id": "create-campaign", "status": outcome},
                {"operation_id": "create-ad-group", "status": outcome},
                {"operation_id": "create-ad", "status": outcome},
            ],
            "has_more": False,
            "mode": "mock",
        }

    def apply_paused_bulk_job(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]:
        return {"submitted": True, "mode": "mock", "approved_by": approved_by, "job": {"id": "mock-bulk-apply", "status": "completed"}, "payload": {**payload, "validate_only": False}, "spend": 0}


@dataclass
class GuardedRealAdsAdapter:
    api_key: str
    base_url: str = settings.ads_base_url

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if params:
            encoded = urlencode([(key, value) for key, value in params.items() if value is not None], doseq=True)
            if encoded:
                url = f"{url}?{encoded}"
        request = Request(url, headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        with urlopen(request, timeout=20) as response:  # noqa: S310 - configured fixed API base URL
            return json.loads(response.read().decode("utf-8"))

    def _post_action(self, path: str, *, activation: bool = False) -> dict[str, Any]:
        if activation and not settings.mutations_enabled:
            raise AdsMutationBlocked("Campaign activation is disabled; require approvals and enable mutations explicitly")
        request = Request(
            f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
            data=b"",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured fixed API base URL
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any], idempotency_key: str, *, validate_only_allowed: bool = False) -> dict[str, Any]:
        if not settings.mutations_enabled and not (validate_only_allowed and payload.get("validate_only") is True):
            raise AdsMutationBlocked("Ads mutations are disabled; set AGENCY_MUTATIONS_ENABLED=true only for an approved paused build")
        request = Request(
            f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json", "Idempotency-Key": idempotency_key},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured fixed API base URL
            return json.loads(response.read().decode("utf-8"))

    def get_account(self) -> dict[str, Any]:
        return self._get("ad_account")

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._get(f"campaigns/{quote(campaign_id, safe='')}", {"include[]": ["serving_issues"]})

    def get_ad_group(self, ad_group_id: str) -> dict[str, Any]:
        return self._get(f"ad_groups/{quote(ad_group_id, safe='')}", {"include[]": ["serving_issues"]})

    def get_ad(self, ad_id: str) -> dict[str, Any]:
        return self._get(f"ads/{quote(ad_id, safe='')}", {"include[]": ["serving_issues"]})

    def activate_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._post_action(f"campaigns/{quote(campaign_id, safe='')}/activate", activation=True)

    def activate_ad_group(self, ad_group_id: str) -> dict[str, Any]:
        return self._post_action(f"ad_groups/{quote(ad_group_id, safe='')}/activate", activation=True)

    def activate_ad(self, ad_id: str) -> dict[str, Any]:
        return self._post_action(f"ads/{quote(ad_id, safe='')}/activate", activation=True)

    def pause_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._post_action(f"campaigns/{quote(campaign_id, safe='')}/pause")

    def pause_account(self) -> dict[str, Any]:
        # Emergency deactivation remains available even while new mutations are disabled.
        return self._post_action("ad_account/pause")

    def get_insights(self, aggregation_level: str, entity_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path_by_level = {"campaign": "campaigns", "ad_group": "ad_groups", "ad": "ads"}
        collection = path_by_level.get(aggregation_level)
        if not collection:
            raise ValueError("Insights aggregation level must be campaign, ad_group, or ad")
        return self._get(f"{collection}/{entity_id}/insights", params)

    def validate_bulk_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("validate_only") is not True:
            raise AdsMutationBlocked("Bulk validation must set validate_only=true")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        job = self._post("bulk_mutation_jobs", payload, f"eve-validate-{digest[:32]}", validate_only_allowed=True)
        return {"submitted": True, "mode": "openai_ads", "job": job, "spend": 0}

    def get_bulk_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"bulk_mutation_jobs/{job_id}")

    def get_bulk_operations(self, job_id: str, after: str | None = None) -> dict[str, Any]:
        return self._get(f"bulk_mutation_jobs/{job_id}/operations", {"limit": 100, "after": after})

    def apply_paused_bulk_job(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]:
        if not settings.mutations_enabled:
            raise AdsMutationBlocked("Ads mutations are disabled; paused build was not submitted")
        if payload.get("validate_only") is not True:
            raise AdsMutationBlocked("Expected immutable validate-only payload")
        apply_payload = {**payload, "validate_only": False}
        if not apply_payload.get("operations"):
            raise AdsMutationBlocked("Paused build has no operations")
        if any(operation.get("input", {}).get("status") != "paused" for operation in apply_payload["operations"]):
            raise AdsMutationBlocked("Eve only submits paused resources")
        digest = hashlib.sha256(json.dumps(apply_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        job = self._post("bulk_mutation_jobs", apply_payload, f"eve-apply-{digest[:32]}")
        return {"submitted": True, "approved_by": approved_by, "mode": "openai_ads", "job": job}


def get_ads_adapter(client_id: str | None = None, session: Any | None = None) -> AdsAdapter:
    if settings.ads_mode == "real":
        if not client_id or session is None:
            raise AdsMutationBlocked("Real Ads mode requires an explicit client-scoped credential lookup")
        from .secret_store import get_client_secret

        api_key = get_client_secret(session, client_id, "OPENAI_ADS_API_KEY")
        if not api_key:
            raise AdsMutationBlocked("No client Ads API key is configured through the secret store")
        return GuardedRealAdsAdapter(api_key, settings.ads_base_url)
    return MockAdsAdapter()
