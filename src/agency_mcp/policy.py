from __future__ import annotations

from dataclasses import dataclass


POLICY_VERSION = "2026-09-10"

# These are intentionally conservative preflight signals, not a replacement for OpenAI review.
REJECT_TERMS = {
    "political": "political advertising is not permitted",
    "gambling": "gambling is restricted",
    "tobacco": "regulated tobacco products are restricted",
    "weapon": "weapons are restricted",
    "adult": "adult content is not permitted",
    "dating": "dating/adult categories are restricted",
    "sexual": "sexual content is not permitted",
    "guaranteed return": "guaranteed financial-return claims are prohibited",
}
MANUAL_TERMS = {
    "health": "health-related advertising may require manual approval",
    "healthcare": "healthcare advertising may require manual approval",
    "medical": "medical advertising may require manual approval",
    "financial": "financial-services advertising may require manual approval",
    "insurance": "insurance advertising may require manual approval",
    "legal": "legal-services advertising may require manual approval",
    "law firm": "legal-services advertising may require manual approval",
    "mortgage": "housing/financial advertising may require manual approval",
}


@dataclass(frozen=True)
class PolicyResult:
    status: str
    reasons: list[str]
    policy_version: str = POLICY_VERSION


def check_advertising_policy(vertical: str, title: str, body: str, destination: str) -> PolicyResult:
    text = " ".join([vertical, title, body, destination]).lower()
    rejected = [reason for term, reason in REJECT_TERMS.items() if term in text]
    if rejected:
        return PolicyResult("rejected", sorted(set(rejected)))
    manual = [reason for term, reason in MANUAL_TERMS.items() if term in text]
    if manual:
        return PolicyResult("manual_review", sorted(set(manual)))
    return PolicyResult("allowed_preflight", ["Preflight passed; final platform review remains authoritative."])
