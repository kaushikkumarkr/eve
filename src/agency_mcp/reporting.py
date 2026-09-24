from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AdsInsightSnapshot,
    BuyerSegment,
    CampaignPlan,
    Client,
    ContextHint,
    ConversationTest,
    ConversionEvent,
    Opportunity,
    SourceDocument,
    VisibilityFinding,
    WorkflowJob,
)


def build_client_report(session: Session, client_id: str) -> dict[str, Any]:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    segments = session.scalars(select(BuyerSegment).where(BuyerSegment.client_id == client_id)).all()
    opportunities = session.scalars(select(Opportunity).where(Opportunity.client_id == client_id)).all()
    findings = session.scalars(select(VisibilityFinding).where(VisibilityFinding.client_id == client_id)).all()
    tests = session.scalars(select(ConversationTest).where(ConversationTest.client_id == client_id)).all()
    plans = session.scalars(select(CampaignPlan).where(CampaignPlan.client_id == client_id)).all()
    hints = session.scalars(select(ContextHint).where(ContextHint.client_id == client_id)).all()
    insights = session.scalars(select(AdsInsightSnapshot).where(AdsInsightSnapshot.client_id == client_id)).all()
    conversions = session.scalars(select(ConversionEvent).where(ConversionEvent.client_id == client_id)).all()
    sources = session.scalars(select(SourceDocument).where(SourceDocument.client_id == client_id)).all()
    jobs = session.scalars(
        select(WorkflowJob).where(WorkflowJob.client_id == client_id).order_by(WorkflowJob.created_at.desc())
    ).all()
    client_mentioned = sum(test.client_mentioned for test in tests)
    return {
        "client": {
            "id": client.id,
            "name": client.name,
            "vertical": client.vertical,
            "website": client.website,
            "status": client.status,
            "profile": client.profile or {},
        },
        "data_quality": {
            "source": "internal research",
            "organic_visibility_metric": "simulated hypothesis, not official OpenAI reporting",
            "redacted_sources": sum(bool(source.redaction_summary) for source in sources),
            "sources_with_hashes": sum(bool(source.content_hash) for source in sources),
        },
        "counts": {
            "sources": len(sources),
            "buyer_segments": len(segments),
            "visibility_findings": len(findings),
            "opportunities": len(opportunities),
            "context_hints": len(hints),
            "campaign_plans": len(plans),
            "insight_snapshots": len(insights),
            "conversion_events": len(conversions),
            "completed_workflows": sum(job.status == "completed" for job in jobs),
        },
        "visibility": {
            "tests": len(tests),
            "client_present_tests": client_mentioned,
            "open_gaps": sum(item.status != "closed" for item in opportunities),
        },
        "opportunities": [
            {"id": item.id, "title": item.title, "category": item.category, "score": item.score, "status": item.status}
            for item in opportunities
        ],
        "next_actions": [
            "Review evidence and approve the highest-scoring opportunity.",
            "Validate the draft creative and landing page before any Ads API mutation.",
            "Keep synthetic visibility results separate from live campaign reporting.",
        ],
        "performance": [
            {"period": item.period, "campaign_external_id": item.campaign_external_id, **item.metrics}
            for item in insights
        ],
        "workflow_status": [
            {"id": job.id, "kind": job.kind, "status": job.status, "error": job.error}
            for job in jobs[:10]
        ],
    }


def report_markdown(report: dict[str, Any]) -> str:
    client = report["client"]
    counts = report["counts"]
    lines = [
        f"# AI Ads Agency Report — {client['name']}",
        "",
        f"Vertical: {client['vertical']}",
        f"Website: {client.get('website') or 'not provided'}",
        "",
        "## Current inventory",
        "",
        *[f"- {key.replace('_', ' ').title()}: {value}" for key, value in counts.items()],
        "",
        "## Visibility",
        "",
        f"- Simulated tests: {report['visibility']['tests']}",
        f"- Client-present tests: {report['visibility']['client_present_tests']}",
        f"- Open gaps: {report['visibility']['open_gaps']}",
        "",
        "## Opportunities",
        "",
    ]
    for opportunity in report["opportunities"]:
        lines.append(f"- **{opportunity['title']}** — score {opportunity['score']:.2f} ({opportunity['status']})")
    lines.extend(["", "## Notes", "", f"- {report['data_quality']['organic_visibility_metric']}", "", "## Next actions", ""])
    lines.extend(f"- {action}" for action in report["next_actions"])
    return "\n".join(lines) + "\n"
