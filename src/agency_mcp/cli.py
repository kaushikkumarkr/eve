from __future__ import annotations

import json

import typer
from sqlalchemy import select

from .db import init_db, session_scope
from .models import Client, Opportunity
from .reporting import build_client_report, report_markdown
from .service import (
    create_campaign_plan,
    create_client,
    get_client_workspace,
    ingest_text,
    preview_campaign,
    update_client_profile,
)
from .config import settings
from .workflows import run_research, run_service_package


app = typer.Typer(help="Internal AI Ads agency operations")


@app.command("db-init")
def db_init() -> None:
    init_db()
    typer.echo("Database initialized")


@app.command("client-create")
def client_create(name: str, vertical: str = "general", website: str | None = None) -> None:
    init_db()
    with session_scope() as session:
        client = create_client(session, name, vertical, website)
        typer.echo(json.dumps({"id": client.id, "name": client.name}, indent=2))


@app.command("client-profile")
def client_profile(client_id: str, profile_json: str) -> None:
    """Merge a JSON profile containing ICP, markets, products, and goals."""
    init_db()
    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("profile_json must be valid JSON") from exc
    if not isinstance(profile, dict):
        raise typer.BadParameter("profile_json must be a JSON object")
    with session_scope() as session:
        client = update_client_profile(session, client_id, profile)
        typer.echo(json.dumps({"id": client.id, "profile": client.profile}, indent=2))


@app.command("clients")
def clients() -> None:
    init_db()
    with session_scope() as session:
        rows = session.scalars(select(Client).order_by(Client.created_at)).all()
        typer.echo(json.dumps([{"id": row.id, "name": row.name, "vertical": row.vertical} for row in rows], indent=2))


@app.command("source-ingest")
def source_ingest(client_id: str, name: str, content: str, kind: str = "text") -> None:
    init_db()
    with session_scope() as session:
        source = ingest_text(session, client_id, name, content, kind)
        typer.echo(json.dumps({"id": source.id, "name": source.name}, indent=2))


@app.command("research")
def research(client_id: str) -> None:
    init_db()
    with session_scope() as session:
        result = run_research(session, client_id)
        typer.echo(json.dumps(result, indent=2))


@app.command("service-run")
def service_run(client_id: str) -> None:
    """Run the complete zero-spend service package for a client."""
    init_db()
    with session_scope() as session:
        typer.echo(json.dumps(run_service_package(session, client_id), indent=2))


@app.command("workspace")
def workspace(client_id: str) -> None:
    """Show a safe operational summary for one client."""
    init_db()
    with session_scope() as session:
        typer.echo(json.dumps(get_client_workspace(session, client_id), indent=2))


@app.command("health")
def health() -> None:
    """Check local configuration and database initialization without spending money."""
    init_db()
    typer.echo(
        json.dumps(
            {
                "database": "ok",
                "research_mode": settings.research_mode,
                "azure_openai_configured": bool(
                    settings.azure_openai_endpoint
                    and settings.azure_openai_api_key
                    and settings.azure_openai_deployment
                ),
                "ads_mode": settings.ads_mode,
                "real_ads_mutations_enabled": settings.mutations_enabled,
            },
            indent=2,
        )
    )


@app.command("plan")
def plan(client_id: str) -> None:
    init_db()
    with session_scope() as session:
        opportunity = session.scalars(select(Opportunity).where(Opportunity.client_id == client_id)).first()
        if not opportunity:
            raise typer.BadParameter("No opportunity found; run research first")
        campaign = create_campaign_plan(session, opportunity.id)
        preview = preview_campaign(session, campaign.id)
        typer.echo(json.dumps(preview, indent=2))


@app.command("report")
def report(client_id: str) -> None:
    init_db()
    with session_scope() as session:
        typer.echo(report_markdown(build_client_report(session, client_id)))


@app.command("demo-seed")
def demo_seed() -> None:
    init_db()
    with session_scope() as session:
        client = create_client(session, "Northstar Home", "consumer_goods", "https://example.com/northstar")
        ingest_text(
            session,
            client.id,
            "customer-research.txt",
            "Customers compare quality and price before buying. They want a reliable option with clear delivery timing. Some buyers switch from an incumbent brand when the product is unavailable or hard to understand.",
            "synthetic",
        )
        typer.echo(json.dumps({"client_id": client.id, "next": [f"agency research {client.id}", f"agency plan {client.id}"]}, indent=2))
