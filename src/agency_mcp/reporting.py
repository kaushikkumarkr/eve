"""Freshness-aware, Ads-only internal reporting.

This module deliberately contains no organic-visibility score, synthetic prompt
simulation, or claim that a context hint matched a particular conversation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .control_plane import _audit, payload_sha256
from .models import (
    AdsInsightSnapshot,
    AdsExternalAction,
    AdsImportRecord,
    AdsManagerConnection,
    AdsReport,
    AdsWorkspace,
    CampaignBlueprint,
    ChangeRequest,
    Client,
    ControlledExperiment,
    Evidence,
    HintSet,
    SourceDocument,
    utc_now,
)


def build_ads_report(session: Session, client_id: str) -> dict[str, Any]:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    workspace = session.scalar(select(AdsWorkspace).where(AdsWorkspace.client_id == client_id))
    manager_access = session.scalar(select(AdsManagerConnection).where(AdsManagerConnection.client_id == client_id))
    sources = session.scalars(select(SourceDocument).where(SourceDocument.client_id == client_id)).all()
    evidence = session.scalars(select(Evidence).where(Evidence.client_id == client_id)).all()
    hint_sets = session.scalars(select(HintSet).where(HintSet.client_id == client_id).order_by(HintSet.created_at.desc())).all()
    blueprints = session.scalars(select(CampaignBlueprint).where(CampaignBlueprint.client_id == client_id).order_by(CampaignBlueprint.created_at.desc())).all()
    changes = session.scalars(select(ChangeRequest).where(ChangeRequest.client_id == client_id).order_by(ChangeRequest.created_at.desc())).all()
    snapshots = session.scalars(select(AdsInsightSnapshot).where(AdsInsightSnapshot.client_id == client_id).order_by(AdsInsightSnapshot.fetched_at.desc())).all()
    experiments = session.scalars(select(ControlledExperiment).where(ControlledExperiment.client_id == client_id).order_by(ControlledExperiment.created_at.desc())).all()
    external_actions = session.scalars(select(AdsExternalAction).where(AdsExternalAction.client_id == client_id).order_by(AdsExternalAction.performed_at.desc())).all()
    imports = session.scalars(select(AdsImportRecord).where(AdsImportRecord.client_id == client_id).order_by(AdsImportRecord.imported_at.desc())).all()
    blockers: list[str] = []
    if not sources:
        blockers.append("No authorized source material is recorded.")
    if not evidence or not any(item.review_status == "approved" for item in evidence):
        blockers.append("No source evidence has a human approval decision.")
    if not workspace and not manager_access:
        blockers.append("No Ads API workspace or operator-attested Ads Manager account access is recorded.")
    elif workspace and workspace.status not in {"verified", "configured"} and not manager_access:
        blockers.append("Ads API workspace has not been verified and no Ads Manager access is recorded.")
    if any(item.freshness_state in {"unsettled", "unknown"} for item in snapshots if item.provider != "mock"):
        blockers.append("One or more recent provider metrics are unsettled and must not drive a final performance conclusion.")
    if any(item.provider == "mock" for item in snapshots):
        blockers.append("Mock metrics are rehearsal data, not campaign performance.")
    return {
        "report_type": "chatgpt_ads_operations",
        "client": {"id": client.id, "name": client.name, "vertical": client.vertical, "website": client.website, "profile": client.profile or {}},
        "readiness": {
            "status": "internal_review_only" if blockers else "reviewable",
            "blockers": blockers,
            "platform_claims": "This report contains only persisted Ads account data and operational records. It does not measure organic ChatGPT recommendations or prompt-level matching.",
        },
        "metric_caveats": [
            "General Insights conversions are click-through conversions; Eve does not currently request the dedicated conversion-insights report that separates view-through conversions.",
            "Provisionally settled data passed Eve's 72-hour buffer but may still be revised by later platform attribution processing.",
            "Ads Manager imports and action records are operator-supplied/attested, not independently verified through the Ads API. Preserve their provider labels in client reporting.",
        ],
        "workspace": {
            "configured": bool(workspace),
            "ad_account_id": workspace.ad_account_id if workspace else None,
            "status": workspace.status if workspace else "missing",
            "observed_at": workspace.observed_at.isoformat() if workspace and workspace.observed_at else None,
            "credentials_exposed": False,
        },
        "ads_manager_access": {
            "status": manager_access.status if manager_access else "not_recorded",
            "ad_account_id": manager_access.ad_account_id if manager_access else None,
            "confirmed_by": manager_access.connected_by if manager_access else None,
            "confirmed_at": manager_access.access_confirmed_at.isoformat() if manager_access else None,
            "verification": "operator_attested_not_platform_verified" if manager_access else "none",
        },
        "evidence": {
            "source_count": len(sources),
            "approved_count": sum(item.review_status == "approved" for item in evidence),
            "pending_count": sum(item.review_status == "unreviewed" for item in evidence),
            "sources": [{"name": item.name, "url": item.source_url, "retrieved_at": item.retrieved_at, "content_hash": item.content_hash} for item in sources],
        },
        "strategy": {
            "hint_sets": [{"id": item.id, "name": item.name, "version": item.version, "status": item.status, "hint_count": len(item.hints), "evidence_ids": item.evidence_ids, "lint": item.lint} for item in hint_sets],
            "blueprints": [{"id": item.id, "name": item.name, "status": item.status, "version": item.version, "payload_sha256": item.compiled_sha256, "validation": item.validation} for item in blueprints],
        },
        "changes": [{"id": item.id, "action": item.action, "status": item.status, "payload_sha256": item.payload_sha256, "client_approval_reference": item.client_approval_reference, "admin_approved_by": item.admin_approved_by, "applied_at": item.applied_at.isoformat() if item.applied_at else None} for item in changes],
        "external_ads_manager_actions": [{"id": item.id, "action_type": item.action_type, "entity_type": item.entity_type, "entity_external_id": item.entity_external_id, "external_reference": item.external_reference, "outcome": item.outcome, "actor_id": item.actor_id, "performed_at": item.performed_at.isoformat(), "verification": "operator_reported_not_api_verified"} for item in external_actions],
        "ads_manager_imports": [{"id": item.id, "ad_account_id": item.ad_account_id, "source_name": item.source_name, "source_reference": item.source_reference, "content_sha256": item.content_sha256, "snapshot_ids": item.snapshot_ids, "imported_by": item.imported_by, "imported_at": item.imported_at.isoformat(), "provider": item.provider} for item in imports],
        "performance": [{"snapshot_id": item.id, "entity_external_id": item.campaign_external_id, "aggregation_level": item.aggregation_level, "period": item.period, "timezone": item.timezone, "provider": item.provider, "verification": "operator_supplied_not_api_verified" if item.provider == "chatgpt_ads_manager_operator_import" else "provider_observed", "freshness_state": item.freshness_state, "metrics": item.metrics, "fetched_at": item.fetched_at.isoformat()} for item in snapshots],
        "controlled_comparisons": [{"id": item.id, "name": item.name, "status": item.status, "changed_variable": item.changed_variable, "primary_metric": item.primary_metric, "decision_rule": item.decision_rule, "arms": item.arms, "result": item.result} for item in experiments],
    }


def create_ads_report(session: Session, client_id: str, generated_by: str | None = None) -> AdsReport:
    """Persist a point-in-time report; content stays immutable after generation."""
    report_json = build_ads_report(session, client_id)
    markdown = report_markdown(report_json)
    content_hash = payload_sha256({"report_json": report_json, "markdown": markdown})
    record = AdsReport(
        client_id=client_id,
        report_json=report_json,
        markdown=markdown,
        content_sha256=content_hash,
        readiness_status=report_json["readiness"]["status"],
        review_status="pending_review",
        generated_by=generated_by,
    )
    session.add(record)
    session.flush()
    _audit(
        session,
        action="ads_report.generated",
        entity_type="ads_report",
        entity_id=record.id,
        client_id=client_id,
        actor_id=generated_by,
        payload={"content_sha256": content_hash, "readiness_status": record.readiness_status},
    )
    session.commit()
    return record


def _verify_report_hash(report: AdsReport) -> None:
    actual = payload_sha256({"report_json": report.report_json, "markdown": report.markdown})
    if actual != report.content_sha256:
        raise ValueError("Persisted report content failed its integrity check")


def review_ads_report(
    session: Session,
    report_id: str,
    decision: str,
    reviewed_by: str,
    notes: str = "",
) -> AdsReport:
    report = session.get(AdsReport, report_id)
    if not report:
        raise ValueError(f"Unknown Ads report: {report_id}")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    if report.review_status != "pending_review":
        raise ValueError("Only a pending Ads report can be reviewed")
    if not reviewed_by.strip():
        raise ValueError("A named human reviewer is required")
    _verify_report_hash(report)
    if decision == "approve" and report.readiness_status != "reviewable":
        raise ValueError("A report with readiness blockers cannot be marked client-ready")
    report.review_status = "client_ready" if decision == "approve" else "rejected"
    report.reviewed_by = reviewed_by.strip()
    report.review_notes = notes.strip()[:5000] or None
    report.reviewed_at = utc_now()
    _audit(
        session,
        action=f"ads_report.{report.review_status}",
        entity_type="ads_report",
        entity_id=report.id,
        client_id=report.client_id,
        actor_id=report.reviewed_by,
        payload={"content_sha256": report.content_sha256, "review_notes": report.review_notes},
    )
    session.commit()
    return report


def mark_ads_report_sent(session: Session, report_id: str, reference: str, sent_by: str) -> AdsReport:
    report = session.get(AdsReport, report_id)
    if not report:
        raise ValueError(f"Unknown Ads report: {report_id}")
    if report.review_status != "client_ready":
        raise ValueError("Only a client-ready Ads report may be recorded as sent")
    if report.sent_at is not None:
        raise ValueError("This Ads report has already been recorded as sent")
    if not reference.strip() or not sent_by.strip():
        raise ValueError("A sent reference and named operator are required")
    _verify_report_hash(report)
    report.sent_reference = reference.strip()[:500]
    report.sent_by = sent_by.strip()
    report.sent_at = utc_now()
    _audit(
        session,
        action="ads_report.sent_recorded",
        entity_type="ads_report",
        entity_id=report.id,
        client_id=report.client_id,
        actor_id=report.sent_by,
        payload={"content_sha256": report.content_sha256, "sent_reference": report.sent_reference},
    )
    session.commit()
    return report


def list_ads_reports(session: Session, client_id: str, limit: int = 20) -> list[AdsReport]:
    return session.scalars(
        select(AdsReport)
        .where(AdsReport.client_id == client_id)
        .order_by(AdsReport.generated_at.desc(), AdsReport.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()


def report_markdown(report: dict[str, Any]) -> str:
    client = report["client"]
    readiness = report["readiness"]
    lines = [
        f"# ChatGPT Ads operations report — {client['name']}",
        "",
        f"Status: **{readiness['status']}**",
        "",
        "## Evidence and scope",
        "",
        readiness["platform_claims"],
        "",
        "### Blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in (readiness["blockers"] or ["No current report blockers."]))
    lines.extend(["", "### Metric caveats", ""])
    lines.extend(f"- {item}" for item in report.get("metric_caveats", []))
    lines.extend(["", "## Campaign control", ""])
    for change in report["changes"]:
        lines.append(f"- `{change['id']}` — {change['action']} — **{change['status']}** — payload `{change['payload_sha256']}`")
    if not report["changes"]:
        lines.append("- No campaign change has been proposed.")
    lines.extend(["", "## Performance snapshots", ""])
    for snapshot in report["performance"]:
        lines.append(f"- {snapshot['aggregation_level']} `{snapshot['entity_external_id']}` / {snapshot['period']} — {snapshot['provider']} — {snapshot['freshness_state']} — {snapshot['metrics']}")
    if not report["performance"]:
        lines.append("- No performance snapshot has been recorded.")
    lines.extend(["", "## Controlled comparisons", ""])
    for experiment in report["controlled_comparisons"]:
        lines.append(f"- {experiment['name']} — {experiment['changed_variable']} — primary KPI: {experiment['primary_metric']} — **{experiment['status']}**")
        if experiment.get("result"):
            result = experiment["result"]
            decision = result.get("decision")
            if decision:
                lines.append(f"  - Data quality: {result.get('data_quality')}; recorded human decision: {decision.get('type')}; causal claim: no.")
            else:
                lines.append(f"  - Data quality: {result.get('data_quality')}; no human decision is recorded; causal claim: no.")
            lines.append(f"  - Compared period: {result.get('period')} ({result.get('timezone')}); metric: {result.get('primary_metric')}.")
            for observation in result.get("arm_observations", []):
                lines.append(f"  - Arm {observation.get('name')}: {observation.get('value')} ({observation.get('freshness_state')}; snapshot `{observation.get('snapshot_id')}`).")
            for warning in result.get("quality_warnings", []):
                lines.append(f"  - Limitation: {warning}")
    if not report["controlled_comparisons"]:
        lines.append("- No controlled comparison is pre-registered.")
    return "\n".join(lines) + "\n"
