from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from .ads import get_ads_adapter
from .auth import build_http_auth, require_admin
from .config import settings
from .db import init_db, session_scope
from .service import (
    approve,
    apply_approved_campaign,
    create_campaign_plan,
    create_client,
    generate_context_hints,
    generate_questions,
    get_client_workspace,
    ingest_text,
    preview_campaign,
    request_approval,
    run_visibility_audit,
    sync_insights,
    update_campaign_plan,
    update_client_profile,
    validate_campaign_plan,
)
from .connectors import ingest_crm_csv
from .measurement import check_conversion_batch, record_conversion, register_conversion_source
from .policy import check_advertising_policy
from .reporting import build_client_report, report_markdown
from .workflows import run_campaign, run_research, run_service_package
from .models import Client, WorkflowJob


_auth_settings, _token_verifier = (
    build_http_auth() if settings.mcp_transport != "stdio" else (None, None)
)
mcp = FastMCP(
    "agency-mcp",
    host=settings.mcp_host,
    port=settings.mcp_port,
    auth=_auth_settings,
    token_verifier=_token_verifier,
)


@mcp.tool()
def create_client_tool(
    name: str,
    vertical: str = "general",
    website: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an internal client record."""
    with session_scope() as session:
        client = create_client(session, name, vertical, website, profile)
        return {
            "id": client.id,
            "name": client.name,
            "vertical": client.vertical,
            "website": client.website,
            "profile": client.profile,
        }


@mcp.tool()
def list_clients_tool() -> dict[str, Any]:
    """List client workspaces without exposing source contents or secrets."""
    with session_scope() as session:
        clients = session.scalars(select(Client).order_by(Client.created_at)).all()
        return {
            "clients": [
                {"id": client.id, "name": client.name, "vertical": client.vertical, "status": client.status}
                for client in clients
            ]
        }


@mcp.tool()
def update_client_profile_tool(client_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Merge ICP, markets, goals, products, and client constraints into a workspace profile."""
    with session_scope() as session:
        client = update_client_profile(session, client_id, profile)
        return {"id": client.id, "profile": client.profile}


@mcp.tool()
def get_client_workspace_tool(client_id: str) -> dict[str, Any]:
    """Return a safe operational summary of one client workspace."""
    with session_scope() as session:
        return get_client_workspace(session, client_id)


@mcp.tool()
def ingest_source(client_id: str, name: str, content: str, kind: str = "text") -> dict[str, Any]:
    """Store authorized client research material and extract source evidence."""
    with session_scope() as session:
        source = ingest_text(session, client_id, name, content, kind)
        return {"id": source.id, "client_id": source.client_id, "name": source.name, "evidence_created": len(source.evidence)}


@mcp.tool()
def run_research_workflow(client_id: str) -> dict[str, Any]:
    """Run the deterministic zero-cost research workflow and return opportunity IDs."""
    with session_scope() as session:
        return run_research(session, client_id)


@mcp.tool()
def run_service_package_tool(client_id: str) -> dict[str, Any]:
    """Run research, visibility-gap analysis, campaign drafting, validation, and dry-run preview."""
    with session_scope() as session:
        return run_service_package(session, client_id)


@mcp.tool()
def list_workflow_jobs_tool(client_id: str, limit: int = 20) -> dict[str, Any]:
    """List durable workflow status for a client, without returning source content."""
    with session_scope() as session:
        jobs = session.scalars(
            select(WorkflowJob)
            .where(WorkflowJob.client_id == client_id)
            .order_by(WorkflowJob.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return {
            "jobs": [
                {"id": job.id, "kind": job.kind, "status": job.status, "error": job.error, "state": job.state}
                for job in jobs
            ]
        }


@mcp.tool()
def run_campaign_workflow(opportunity_id: str) -> dict[str, Any]:
    """Draft, validate, and dry-run preview a campaign through LangGraph."""
    with session_scope() as session:
        return run_campaign(session, opportunity_id)


@mcp.tool()
def run_visibility_audit_tool(client_id: str, questions: list[str] | None = None) -> dict[str, Any]:
    """Run synthetic visibility tests; results are hypotheses, not official OpenAI metrics."""
    with session_scope() as session:
        tests = run_visibility_audit(session, client_id, questions)
        return {"provider": "synthetic", "test_ids": [test.id for test in tests], "count": len(tests)}


@mcp.tool()
def generate_hints(opportunity_id: str) -> dict[str, Any]:
    """Draft context hints from an approved or reviewable opportunity."""
    with session_scope() as session:
        hints = generate_context_hints(session, opportunity_id)
        return {"hint_ids": [hint.id for hint in hints], "hints": [hint.text for hint in hints]}


@mcp.tool()
def create_campaign_plan_tool(opportunity_id: str) -> dict[str, Any]:
    """Create a paused, draft-only campaign plan."""
    with session_scope() as session:
        plan = create_campaign_plan(session, opportunity_id)
        return {"id": plan.id, "status": plan.status, "objective": plan.objective, "campaign_name": plan.campaign_name}


@mcp.tool()
def update_campaign_plan_tool(
    plan_id: str,
    budget: dict[str, Any] | None = None,
    targeting: dict[str, Any] | None = None,
    creative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a draft plan with explicit client inputs; this never activates or spends."""
    with session_scope() as session:
        plan = update_campaign_plan(session, plan_id, budget, targeting, creative)
        return {
            "id": plan.id,
            "status": plan.status,
            "budget": plan.budget,
            "targeting": plan.targeting,
            "creative": plan.creative,
        }


@mcp.tool()
def validate_campaign(plan_id: str) -> dict[str, Any]:
    """Validate creative and campaign fields without contacting Ads."""
    with session_scope() as session:
        return validate_campaign_plan(session, plan_id)


@mcp.tool()
def preview_ads_change(plan_id: str) -> dict[str, Any]:
    """Preview the paused Ads payload; never spends or mutates an Ads account."""
    with session_scope() as session:
        return preview_campaign(session, plan_id)


@mcp.tool()
def request_approval_tool(entity_type: str, entity_id: str, action: str) -> dict[str, Any]:
    """Create an explicit approval request for a future mutation."""
    with session_scope() as session:
        approval = request_approval(session, entity_type, entity_id, action)
        return {"id": approval.id, "status": approval.status, "action": approval.action}


@mcp.tool()
def approve_tool(approval_id: str, approved_by: str) -> dict[str, Any]:
    """Record human approval; this does not itself activate or spend on Ads."""
    require_admin()
    with session_scope() as session:
        approval = approve(session, approval_id, approved_by)
        return {"id": approval.id, "status": approval.status, "approved_by": approval.approved_by}


@mcp.tool()
def apply_approved_campaign_tool(plan_id: str, approval_id: str, approved_by: str) -> dict[str, Any]:
    """Apply only an explicitly approved campaign plan; resources remain paused."""
    require_admin()
    with session_scope() as session:
        return apply_approved_campaign(session, plan_id, approval_id, approved_by)


@mcp.tool()
def generate_report(client_id: str) -> dict[str, Any]:
    """Generate a structured internal/client report from persisted records."""
    with session_scope() as session:
        report = build_client_report(session, client_id)
        return {"json": report, "markdown": report_markdown(report)}


@mcp.tool()
def sync_ads_account(client_id: str | None = None) -> dict[str, Any]:
    """Read the configured Ads account; mock mode is used unless explicitly configured otherwise."""
    if client_id:
        with session_scope() as session:
            return get_ads_adapter(client_id, session).get_account()
    return get_ads_adapter().get_account()


@mcp.tool()
def ingest_crm_csv_tool(client_id: str, provider: str, filename: str, csv_content: str) -> dict[str, Any]:
    """Ingest CRM notes while excluding direct identifiers such as email and phone by default."""
    with session_scope() as session:
        result = ingest_crm_csv(session, client_id, provider, filename, csv_content)
        return result.__dict__


@mcp.tool()
def policy_preflight(vertical: str, title: str, body: str, destination: str) -> dict[str, Any]:
    """Run conservative policy preflight; final platform review remains authoritative."""
    result = check_advertising_policy(vertical, title, body, destination)
    return {"status": result.status, "reasons": result.reasons, "policy_version": result.policy_version}


@mcp.tool()
def register_conversion_source_tool(client_id: str, source_type: str, external_id: str, name: str) -> dict[str, Any]:
    """Register a Pixel, CAPI, or CRM conversion source without sending an event."""
    with session_scope() as session:
        source = register_conversion_source(session, client_id, source_type, external_id, name)
        return {"id": source.id, "client_id": source.client_id, "source_type": source.source_type, "external_id": source.external_id}


@mcp.tool()
def record_conversion_tool(
    client_id: str,
    source_id: str,
    event_id: str,
    event_type: str,
    value_minor: int | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Record a conversion with browser/server-style event-id deduplication."""
    with session_scope() as session:
        return record_conversion(session, client_id, source_id, event_id, event_type, value_minor, currency)


@mcp.tool()
def check_conversion_batch_tool(
    client_id: str,
    pixel_id: str,
    events: list[dict[str, Any]],
    validate_only: bool = True,
) -> dict[str, Any]:
    """Locally or remotely validate a Conversions API batch; live submission requires admin role."""
    if not validate_only:
        require_admin()
    with session_scope() as session:
        return check_conversion_batch(session, client_id, pixel_id, events, validate_only)


@mcp.tool()
def sync_ads_insights_tool(client_id: str, campaign_external_id: str, period: str = "latest") -> dict[str, Any]:
    """Sync campaign insights; mock mode returns zero-spend metrics."""
    with session_scope() as session:
        return sync_insights(session, client_id, campaign_external_id, period)


def main() -> None:
    init_db()
    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
