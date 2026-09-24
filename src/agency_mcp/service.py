from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ads import AdsMutationBlocked, get_ads_adapter
from .config import settings
from .llm import get_research_provider
from .models import (
    Approval,
    AuditLog,
    BuyerSegment,
    CampaignPlan,
    Client,
    ContextHint,
    ConversationTest,
    Evidence,
    Opportunity,
    SourceDocument,
    VisibilityFinding,
    PolicyCheck,
    AdsInsightSnapshot,
)
from .policy import check_advertising_policy


def _commit(session: Session, action: str, entity_type: str, entity_id: str | None, payload: dict[str, Any]) -> None:
    session.add(AuditLog(action=action, entity_type=entity_type, entity_id=entity_id, payload=payload))
    session.commit()


def _sentences(content: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", content) if part.strip()]


def create_client(session: Session, name: str, vertical: str = "general", website: str | None = None) -> Client:
    client = Client(name=name, vertical=vertical, website=website)
    session.add(client)
    session.flush()
    _commit(session, "client.created", "client", client.id, {"name": name, "vertical": vertical})
    return client


def ingest_text(session: Session, client_id: str, name: str, content: str, kind: str = "text") -> SourceDocument:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    source = SourceDocument(client_id=client_id, name=name, kind=kind, content=content)
    session.add(source)
    session.flush()
    for sentence in _sentences(content):
        session.add(Evidence(client_id=client_id, source_document_id=source.id, statement=sentence, confidence=0.65))
    _commit(session, "source.ingested", "source_document", source.id, {"name": name, "kind": kind})
    return source


def extract_voc(session: Session, client_id: str) -> list[BuyerSegment]:
    sources = session.scalars(select(SourceDocument).where(SourceDocument.client_id == client_id)).all()
    combined = " ".join(source.content for source in sources).lower()
    patterns = [
        ("comparison buyer", "A buyer comparing alternatives and weighing tradeoffs.", ["compare", "alternative", "switch"]),
        ("urgent buyer", "A buyer with a time-sensitive problem and a need for a fast solution.", ["urgent", "immediately", "deadline", "today"]),
        ("value-conscious buyer", "A buyer balancing price, quality, and expected return.", ["price", "budget", "affordable", "cost"]),
        ("quality-focused buyer", "A buyer prioritizing reliability, quality, and fit.", ["quality", "reliable", "best", "premium"]),
    ]
    segments: list[BuyerSegment] = []
    for name, description, keywords in patterns:
        if any(keyword in combined for keyword in keywords):
            segment = BuyerSegment(
                client_id=client_id,
                name=name,
                description=description,
                attributes={"matched_keywords": [keyword for keyword in keywords if keyword in combined], "status": "inferred"},
            )
            session.add(segment)
            segments.append(segment)
    if not segments:
        segment = BuyerSegment(
            client_id=client_id,
            name="general evaluator",
            description="A buyer evaluating whether the client is a suitable solution.",
            attributes={"status": "inferred"},
        )
        session.add(segment)
        segments.append(segment)
    session.flush()
    _commit(session, "voc.extracted", "client", client_id, {"segment_ids": [segment.id for segment in segments]})
    return segments


def generate_questions(session: Session, client_id: str) -> list[str]:
    segments = session.scalars(select(BuyerSegment).where(BuyerSegment.client_id == client_id)).all()
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    questions = [f"What should a {segment.name} look for when choosing a {client.vertical} solution?" for segment in segments]
    provider = get_research_provider()
    if provider and settings.research_mode.lower() == "openai":
        context = "\n".join(segment.description for segment in segments)
        result = provider.generate_json(
            "Generate 3 to 8 high-intent buyer questions as JSON with a top-level `questions` array. "
            "Questions must be realistic, non-branded, and suitable for visibility research.",
            context,
        )
        candidate_questions = result.value.get("questions", []) if isinstance(result.value, dict) else []
        if candidate_questions and all(isinstance(item, str) and item.strip() for item in candidate_questions):
            questions = candidate_questions[:8]
    if not questions:
        questions = [f"What are the best options for a {client.vertical} buyer evaluating solutions?"]
    _commit(session, "questions.generated", "client", client_id, {"questions": questions, "status": "simulated"})
    return questions


def run_visibility_audit(session: Session, client_id: str, questions: list[str] | None = None) -> list[ConversationTest]:
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    questions = questions or generate_questions(session, client_id)
    tests: list[ConversationTest] = []
    for question in questions:
        competitors = ["Established category leader", "Specialist alternative"]
        answer = f"Synthetic audit for: {question}. Consider {', '.join(competitors)} and evaluate fit, price, quality, and evidence."
        test = ConversationTest(
            client_id=client_id,
            question=question,
            provider="synthetic",
            answer=answer,
            brands=competitors,
            client_mentioned=False,
        )
        session.add(test)
        tests.append(test)
    session.flush()
    _commit(session, "visibility.audit.completed", "client", client_id, {"test_ids": [test.id for test in tests], "provider": "synthetic"})
    return tests


def identify_gaps(session: Session, client_id: str) -> list[Opportunity]:
    tests = session.scalars(select(ConversationTest).where(ConversationTest.client_id == client_id)).all()
    opportunities: list[Opportunity] = []
    for test in tests:
        finding = VisibilityFinding(
            client_id=client_id,
            conversation_test_id=test.id,
            finding_type="client_absent",
            summary="Competitors appear in a simulated high-intent answer while the client is absent.",
            confidence=0.55,
        )
        session.add(finding)
        session.flush()
        opportunity = Opportunity(
            client_id=client_id,
            title=f"Backfill visibility for: {test.question}",
            category="paid_visibility",
            score=0.55,
            evidence_ids=[finding.id, test.id],
        )
        session.add(opportunity)
        opportunities.append(opportunity)
    session.flush()
    _commit(session, "visibility.gaps.created", "client", client_id, {"opportunity_ids": [item.id for item in opportunities]})
    return opportunities


def generate_context_hints(session: Session, opportunity_id: str) -> list[ContextHint]:
    opportunity = session.get(Opportunity, opportunity_id)
    if not opportunity:
        raise ValueError(f"Unknown opportunity: {opportunity_id}")
    client = session.get(Client, opportunity.client_id)
    if not client:
        raise ValueError(f"Unknown client: {opportunity.client_id}")
    hint_text = (
        f"Show this ad to people actively evaluating {client.vertical} options who are comparing alternatives, "
        "care about fit and quality, and want a clear next step from a credible provider."
    )
    hint = ContextHint(client_id=client.id, opportunity_id=opportunity.id, text=hint_text, confidence=0.55)
    session.add(hint)
    session.flush()
    _commit(session, "context_hint.generated", "context_hint", hint.id, {"status": "draft", "evidence_ids": opportunity.evidence_ids})
    return [hint]


def create_campaign_plan(session: Session, opportunity_id: str) -> CampaignPlan:
    opportunity = session.get(Opportunity, opportunity_id)
    if not opportunity:
        raise ValueError(f"Unknown opportunity: {opportunity_id}")
    client = session.get(Client, opportunity.client_id)
    hints = session.scalars(select(ContextHint).where(ContextHint.opportunity_id == opportunity.id)).all()
    if not hints:
        hints = generate_context_hints(session, opportunity.id)
    plan = CampaignPlan(
        client_id=client.id,
        opportunity_id=opportunity.id,
        objective="clicks",
        campaign_name=f"{client.name} - AI visibility test",
        ad_group_name="High-intent evaluation",
        budget={"daily_spend_limit_micros": 0, "currency": "USD", "requires_client_approval": True},
        targeting={"platforms": ["web"], "locations": {"countries": ["US"]}},
        creative={
            "type": "chat_card",
            "title": f"Explore {client.name}",
            "body": "Evaluate a relevant option for your needs.",
            "target_url": client.website or "https://example.com",
        },
    )
    session.add(plan)
    session.flush()
    _commit(session, "campaign_plan.created", "campaign_plan", plan.id, {"objective": plan.objective, "hint_ids": [hint.id for hint in hints]})
    return plan


def validate_campaign_plan(session: Session, plan_id: str) -> dict[str, Any]:
    plan = session.get(CampaignPlan, plan_id)
    if not plan:
        raise ValueError(f"Unknown campaign plan: {plan_id}")
    errors: list[str] = []
    warnings: list[str] = []
    client = session.get(Client, plan.client_id)
    title = plan.creative.get("title", "")
    body = plan.creative.get("body", "")
    target_url = plan.creative.get("target_url", "")
    if not 3 <= len(title) <= 50:
        errors.append("creative.title must be 3-50 characters")
    if len(body) > 100:
        errors.append("creative.body must be at most 100 characters")
    if not target_url.startswith(("http://", "https://")):
        errors.append("creative.target_url must use http or https")
    policy = check_advertising_policy(
        client.vertical if client else "general",
        title,
        body,
        target_url,
    )
    session.add(
        PolicyCheck(
            client_id=plan.client_id,
            entity_type="campaign_plan",
            entity_id=plan.id,
            status=policy.status,
            policy_version=policy.policy_version,
            reasons=policy.reasons,
        )
    )
    if policy.status == "rejected":
        errors.extend(policy.reasons)
    elif policy.status == "manual_review":
        warnings.extend(policy.reasons)
    if plan.objective == "conversions":
        warnings.append("conversion objective requires one active standard conversion event setting")
    if not plan.budget.get("daily_spend_limit_micros"):
        warnings.append("an explicit positive budget is required before any real Ads submission")
    result = {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "policy_status": policy.status,
        "policy_version": policy.policy_version,
        "plan_id": plan_id,
    }
    _commit(session, "campaign_plan.validated", "campaign_plan", plan.id, result)
    return result


def sync_insights(session: Session, client_id: str, campaign_external_id: str, period: str = "latest") -> dict[str, Any]:
    metrics = get_ads_adapter().get_insights(campaign_external_id)
    snapshot = AdsInsightSnapshot(
        client_id=client_id,
        campaign_external_id=campaign_external_id,
        period=period,
        metrics=metrics,
        provider=settings.ads_mode,
    )
    session.add(snapshot)
    _commit(session, "ads.insights.synced", "ads_insight_snapshot", snapshot.id, {"campaign_external_id": campaign_external_id, "period": period})
    return {"id": snapshot.id, "client_id": client_id, "campaign_external_id": campaign_external_id, "period": period, "metrics": metrics}


def preview_campaign(session: Session, plan_id: str) -> dict[str, Any]:
    plan = session.get(CampaignPlan, plan_id)
    if not plan:
        raise ValueError(f"Unknown campaign plan: {plan_id}")
    validation = validate_campaign_plan(session, plan_id)
    hints = session.scalars(
        select(ContextHint).where(
            ContextHint.client_id == plan.client_id,
            ContextHint.opportunity_id == plan.opportunity_id,
        )
    ).all()
    payload = {
        "campaign": {
            "name": plan.campaign_name,
            "status": "paused",
            "bidding_type": plan.objective,
            "budget": plan.budget,
            "targeting": plan.targeting,
        },
        "ad_group": {
            "name": plan.ad_group_name,
            "status": "paused",
            "context_hints": [hint.text for hint in hints],
            "bidding_config": {
                "billing_event_type": "click",
                "strategy": "fixed_bid",
                "max_bid_micros": 0,
                "requires_client_approval": True,
            },
        },
        "ad": {"status": "paused", "creative": plan.creative},
    }
    preview = get_ads_adapter().preview_mutation(payload)
    result = {"validation": validation, "ads_preview": preview, "approval_required": True, "spend": 0}
    _commit(session, "campaign.previewed", "campaign_plan", plan.id, result)
    return result


def request_approval(session: Session, entity_type: str, entity_id: str, action: str) -> Approval:
    approval = Approval(entity_type=entity_type, entity_id=entity_id, action=action, status="pending")
    session.add(approval)
    session.flush()
    _commit(session, "approval.requested", entity_type, entity_id, {"approval_id": approval.id, "action": action})
    return approval


def approve(session: Session, approval_id: str, approved_by: str) -> Approval:
    approval = session.get(Approval, approval_id)
    if not approval:
        raise ValueError(f"Unknown approval: {approval_id}")
    approval.status = "approved"
    approval.approved_by = approved_by
    _commit(session, "approval.granted", approval.entity_type, approval.entity_id, {"approval_id": approval.id, "approved_by": approved_by})
    return approval


def apply_approved_campaign(session: Session, plan_id: str, approval_id: str, approved_by: str) -> dict[str, Any]:
    plan = session.get(CampaignPlan, plan_id)
    approval = session.get(Approval, approval_id)
    if not plan:
        raise ValueError(f"Unknown campaign plan: {plan_id}")
    if not approval or approval.entity_type != "campaign_plan" or approval.entity_id != plan_id:
        raise ValueError("Approval does not match the campaign plan")
    if approval.status != "approved":
        raise ValueError("Campaign plan requires an approved approval record")
    validation = validate_campaign_plan(session, plan_id)
    if not validation["valid"]:
        raise ValueError(f"Campaign plan is invalid: {validation['errors']}")
    payload = {
        "campaign": {
            "name": plan.campaign_name,
            "status": "paused",
            "bidding_type": plan.objective,
            "budget": plan.budget,
            "targeting": plan.targeting,
        },
        "ad_group": {
            "name": plan.ad_group_name,
            "status": "paused",
            "context_hints": [hint.text for hint in session.scalars(select(ContextHint).where(ContextHint.opportunity_id == plan.opportunity_id)).all()],
            "bidding_config": {
                "billing_event_type": "click",
                "strategy": "fixed_bid",
                "max_bid_micros": 0,
            },
        },
        "ad": {"status": "paused", "creative": plan.creative},
    }
    try:
        result = get_ads_adapter().apply_mutation(payload, approved_by)
    except AdsMutationBlocked:
        raise
    plan.status = "submitted"
    _commit(session, "campaign.applied", "campaign_plan", plan.id, {"approval_id": approval.id, "approved_by": approved_by, "result": result})
    return {"plan_id": plan.id, "status": plan.status, "spend": 0, "result": result}
