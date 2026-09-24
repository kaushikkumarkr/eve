from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.request import Request, urlopen

from .config import settings


class AdsMutationBlocked(RuntimeError):
    """Raised whenever an unapproved or disabled Ads mutation is attempted."""


class AdsAdapter(Protocol):
    def get_account(self) -> dict[str, Any]: ...

    def list_campaigns(self) -> list[dict[str, Any]]: ...

    def get_insights(self, campaign_id: str) -> dict[str, Any]: ...

    def preview_mutation(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def apply_mutation(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]: ...


@dataclass
class MockAdsAdapter:
    account_id: str = "mock_account"

    def get_account(self) -> dict[str, Any]:
        return {
            "id": self.account_id,
            "name": "Synthetic Test Advertiser",
            "status": "active",
            "brand_review": "approved",
            "currency": "USD",
            "timezone": "America/New_York",
            "spend": 0,
            "mode": "mock",
        }

    def list_campaigns(self) -> list[dict[str, Any]]:
        return []

    def get_insights(self, campaign_id: str) -> dict[str, Any]:
        return {
            "campaign_id": campaign_id,
            "impressions": 0,
            "clicks": 0,
            "spend": 0.0,
            "ctr": 0.0,
            "cpc": None,
            "conversions": 0,
            "provider": "mock",
        }

    def preview_mutation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"dry_run": True, "would_apply": payload, "spend": 0}

    def apply_mutation(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]:
        # Mock application is always safe: it records the approval path but never contacts Ads.
        return {"applied": True, "approved_by": approved_by, "payload": payload, "spend": 0, "mode": "mock"}


@dataclass
class GuardedRealAdsAdapter:
    """Read-only placeholder for the official Advertiser API.

    The adapter is deliberately incomplete until credentials and a live account are supplied.
    It still provides a safe contract for the rest of the system and rejects writes by default.
    """

    api_key: str
    base_url: str = settings.ads_base_url

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        with urlopen(request, timeout=20) as response:  # noqa: S310 - URL comes from configured settings
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not settings.mutations_enabled:
            raise AdsMutationBlocked("Ads mutations are disabled; set AGENCY_MUTATIONS_ENABLED=true explicitly")
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URL comes from configured settings
            return json.loads(response.read().decode("utf-8"))

    def get_account(self) -> dict[str, Any]:
        return self._get("ad_account")

    def list_campaigns(self) -> list[dict[str, Any]]:
        result = self._get("campaigns")
        if isinstance(result, list):
            return result
        return result.get("data", [])

    def get_insights(self, campaign_id: str) -> dict[str, Any]:
        return self._get(f"campaigns/{campaign_id}/insights")

    def preview_mutation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"dry_run": True, "would_apply": payload, "live": True}

    def apply_mutation(self, payload: dict[str, Any], approved_by: str) -> dict[str, Any]:
        if not settings.mutations_enabled:
            raise AdsMutationBlocked("Ads mutations are disabled; set AGENCY_MUTATIONS_ENABLED=true explicitly")
        campaign = dict(payload["campaign"])
        ad_group = dict(payload["ad_group"])
        ad = dict(payload["ad"])
        if not campaign.get("budget", {}).get("daily_spend_limit_micros"):
            raise AdsMutationBlocked("A positive approved daily budget is required for a real Ads mutation")
        if not ad_group.get("bidding_config", {}).get("max_bid_micros"):
            raise AdsMutationBlocked("A positive approved bid is required for a real Ads mutation")
        for resource in (campaign, ad_group, ad):
            resource["status"] = "paused"
        root_key = f"agency-{uuid.uuid4()}"
        campaign_result = self._post("campaigns", campaign, f"{root_key}-campaign")
        campaign_id = campaign_result.get("id")
        if not campaign_id:
            raise RuntimeError("Ads API campaign response did not include an id")
        ad_group["campaign_id"] = campaign_id
        ad_group_result = self._post("ad_groups", ad_group, f"{root_key}-ad-group")
        ad_group_id = ad_group_result.get("id")
        if not ad_group_id:
            raise RuntimeError("Ads API ad-group response did not include an id")
        ad["ad_group_id"] = ad_group_id
        ad_result = self._post("ads", ad, f"{root_key}-ad")
        return {
            "applied": True,
            "approved_by": approved_by,
            "campaign": campaign_result,
            "ad_group": ad_group_result,
            "ad": ad_result,
        }


def get_ads_adapter() -> AdsAdapter:
    if settings.ads_mode == "real" and settings.ads_api_key:
        return GuardedRealAdsAdapter(settings.ads_api_key, settings.ads_base_url)
    return MockAdsAdapter()
