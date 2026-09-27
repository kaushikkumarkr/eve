"""Human-admin CLI for Eve's Ads-only control plane.

MCP is the normal operator interface. This CLI owns secure credential intake and
is the deliberate fallback for privileged, human-reviewed actions.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from getpass import getpass
from typing import Any

import typer
from sqlalchemy import select

from .auth import current_actor_id, require_admin
from .config import settings
from .control_plane import (
    admin_approve_change,
    compile_blueprint,
    configure_ads_workspace,
    create_campaign_blueprint,
    create_controlled_experiment,
    evaluate_controlled_experiment,
    create_hint_set,
    grant_client_access,
    propose_build_change,
    record_client_approval,
    record_controlled_experiment_decision,
    verify_audit_chain,
    workspace_summary,
)
from .db import (
    BOOTSTRAP_FAILURE_EXIT_CODES,
    BootstrapFailure,
    bootstrap_runtime_database,
    migrate_db,
    session_scope,
    verify_db_schema,
)
from .executor import list_executor_jobs, queue_executor_job, run_executor_once, run_pending_executor_jobs
from .models import Client, ControlledExperiment
from .reporting import create_ads_report, mark_ads_report_sent, review_ads_report
from .remote_secret_client import remote_secret_request, validate_remote_secret_destination
from .secret_store import (
    delete_client_secret,
    delete_key_vault_client_secret,
    generate_master_key,
    list_client_secret_names,
    set_client_secret,
    write_key_vault_client_secret,
)
from .service import create_client, ingest_text, review_evidence, update_client_profile
from .workflows import run_blueprint_preflight, run_workspace_readiness


app = typer.Typer(help="Eve: private, Ads-only ChatGPT Ads control plane")


def _json(value: str, name: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{name} must be valid JSON") from exc


def _admin() -> None:
    require_admin()


@app.callback()
def _privileged_cli_boundary(ctx: typer.Context) -> None:
    """The host-side CLI is an admin surface; team operators use MCP instead."""
    if ctx.resilient_parsing or ctx.invoked_subcommand in {
        None,
        "health",
        "secrets-keygen",
        "executor-once",
        "executor-drain",
        "executor-worker",
        "db-bootstrap-runtime",
        "secret-set",
        "secret-list",
        "secret-delete",
    }:
        return
    _admin()


@app.command("db-init")
def db_init() -> None:
    migrate_db()
    typer.echo("Database is at the current Alembic revision")


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Admin-only explicit schema migration; application services never migrate on startup."""
    migrate_db()
    typer.echo("Database is at the current Alembic revision")


@app.command("db-bootstrap-runtime")
def db_bootstrap_runtime() -> None:
    """One-shot deployment bootstrap: migrate PostgreSQL and write its runtime URL to Key Vault."""
    if settings.process_role != "bootstrap":
        raise typer.BadParameter("This command is restricted to EVE_PROCESS_ROLE=bootstrap")
    if settings.secret_backend != "azure_key_vault" or not settings.azure_key_vault_url:
        raise typer.BadParameter("Azure Key Vault must be configured for the bootstrap job")
    secret_name = os.getenv("EVE_DATABASE_RUNTIME_SECRET_NAME", "eve-database-url")
    def emit_safe_event(code: str) -> None:
        typer.echo(f"EVE_BOOTSTRAP_EVENT {code}", err=True)

    try:
        bootstrap_runtime_database(
            secret_name=secret_name,
            vault_url=settings.azure_key_vault_url,
            emit_event=emit_safe_event,
        )
    except BootstrapFailure as exc:
        # Fixed exit codes let Azure report the failed bootstrap stage without exposing
        # exception text, connection details, or provider diagnostics.
        emit_safe_event(f"failed_{exc.stage}")
        raise typer.Exit(code=BOOTSTRAP_FAILURE_EXIT_CODES[exc.stage]) from None
    except Exception:
        # Bootstrap internals intentionally raise only stage labels; never emit driver,
        # identity, URL, SQL, or cloud-provider exception details from this job.
        raise typer.Exit(code=1) from None
    typer.echo("Eve database bootstrap succeeded")


@app.command("health")
def health() -> None:
    """Check configuration without exposing secrets or sending an API request."""
    verify_db_schema()
    typer.echo(json.dumps({
        "database": "ok",
        "ads_mode": settings.ads_mode,
        "mutations_enabled": settings.mutations_enabled,
        "mcp_transport": settings.mcp_transport,
        "role": settings.agency_role,
        "operator_id": settings.agency_operator_id,
        "secret_backend": settings.secret_backend,
        "local_master_key_configured": bool(settings.agency_master_key),
        "azure_key_vault_configured": bool(settings.azure_key_vault_url),
    }, indent=2))


@app.command("audit-verify")
def audit_verify() -> None:
    """Verify the append-only audit hash chain without printing event contents."""
    _admin()
    verify_db_schema()
    with session_scope() as session:
        result = verify_audit_chain(session)
        typer.echo(json.dumps(result, indent=2))
        if result["status"] != "verified":
            raise typer.Exit(code=2)


@app.command("client-create")
def client_create(name: str, vertical: str = "general", website: str | None = None) -> None:
    _admin()
    verify_db_schema()
    with session_scope() as session:
        client = create_client(session, name, vertical, website)
        typer.echo(json.dumps({"id": client.id, "name": client.name}, indent=2))


@app.command("clients")
def clients() -> None:
    verify_db_schema()
    with session_scope() as session:
        rows = session.scalars(select(Client).order_by(Client.created_at)).all()
        typer.echo(json.dumps([{"id": row.id, "name": row.name, "vertical": row.vertical, "status": row.status} for row in rows], indent=2))


@app.command("client-access-grant")
def client_access_grant(client_id: str, operator_id: str, role: str = "operator") -> None:
    """Admin-only client assignment for an individual authenticated operator."""
    _admin()
    verify_db_schema()
    with session_scope() as session:
        grant = grant_client_access(session, client_id, operator_id, role, current_actor_id())
        typer.echo(json.dumps({"id": grant.id, "client_id": grant.client_id, "operator_id": grant.operator_id, "role": grant.role}, indent=2))


@app.command("client-profile")
def client_profile(client_id: str, profile_json: str) -> None:
    verify_db_schema()
    profile = _json(profile_json, "profile_json")
    if not isinstance(profile, dict):
        raise typer.BadParameter("profile_json must be a JSON object")
    with session_scope() as session:
        client = update_client_profile(session, client_id, profile)
        typer.echo(json.dumps({"id": client.id, "profile": client.profile}, indent=2))


@app.command("source-ingest")
def source_ingest(client_id: str, name: str, content: str, kind: str = "text", source_url: str | None = None, retrieved_at: str | None = None) -> None:
    verify_db_schema()
    with session_scope() as session:
        source = ingest_text(session, client_id, name, content, kind, source_url=source_url, retrieved_at=retrieved_at)
        typer.echo(json.dumps({"id": source.id, "content_hash": source.content_hash, "redactions": source.redaction_summary, "evidence_count": len(source.evidence)}, indent=2))


@app.command("evidence-review")
def evidence_review(evidence_id: str, decision: str) -> None:
    _admin()
    verify_db_schema()
    with session_scope() as session:
        evidence = review_evidence(session, evidence_id, decision, current_actor_id())
        typer.echo(json.dumps({"id": evidence.id, "review_status": evidence.review_status}, indent=2))


@app.command("ads-workspace-configure")
def ads_workspace_configure(client_id: str, ad_account_id: str | None = None, credential_name: str = "OPENAI_ADS_API_KEY") -> None:
    verify_db_schema()
    with session_scope() as session:
        workspace = configure_ads_workspace(session, client_id, ad_account_id=ad_account_id, credential_name=credential_name, actor_id=current_actor_id())
        typer.echo(json.dumps({"id": workspace.id, "ad_account_id": workspace.ad_account_id, "status": workspace.status}, indent=2))


@app.command("workspace")
def workspace(client_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(workspace_summary(session, client_id), indent=2, default=str))


@app.command("workspace-readiness")
def workspace_readiness(client_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(run_workspace_readiness(session, client_id), indent=2, default=str))


@app.command("hint-draft")
def hint_draft(client_id: str, name: str, hints_json: str, evidence_ids_json: str = "[]", rationale: str = "") -> None:
    verify_db_schema()
    hints = _json(hints_json, "hints_json")
    evidence_ids = _json(evidence_ids_json, "evidence_ids_json")
    if not isinstance(hints, list) or not isinstance(evidence_ids, list):
        raise typer.BadParameter("hints_json and evidence_ids_json must be arrays")
    with session_scope() as session:
        hint_set = create_hint_set(session, client_id, name, hints, evidence_ids=evidence_ids, rationale=rationale, actor_id=current_actor_id())
        typer.echo(json.dumps({"id": hint_set.id, "version": hint_set.version, "status": hint_set.status, "lint": hint_set.lint}, indent=2))


@app.command("blueprint-draft")
def blueprint_draft(client_id: str, workspace_id: str, hint_set_id: str, name: str, desired_state_json: str) -> None:
    verify_db_schema()
    desired_state = _json(desired_state_json, "desired_state_json")
    if not isinstance(desired_state, dict):
        raise typer.BadParameter("desired_state_json must be an object")
    with session_scope() as session:
        blueprint = create_campaign_blueprint(session, client_id, workspace_id, hint_set_id, name, desired_state, actor_id=current_actor_id())
        typer.echo(json.dumps({"id": blueprint.id, "status": blueprint.status, "version": blueprint.version}, indent=2))


@app.command("blueprint-compile")
def blueprint_compile(blueprint_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(compile_blueprint(session, blueprint_id, current_actor_id()), indent=2, default=str))


@app.command("blueprint-preflight")
def blueprint_preflight(blueprint_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(run_blueprint_preflight(session, blueprint_id, current_actor_id()), indent=2, default=str))


@app.command("change-propose")
def change_propose(blueprint_id: str, rationale: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        request = propose_build_change(session, blueprint_id, rationale, current_actor_id())
        typer.echo(json.dumps({"id": request.id, "status": request.status, "payload_sha256": request.payload_sha256, "expires_at": request.expires_at}, indent=2, default=str))


@app.command("change-validate")
def change_validate(change_id: str) -> None:
    """Queue OpenAI Ads validate_only. The private executor performs the zero-spend call."""
    verify_db_schema()
    with session_scope() as session:
        from .models import ChangeRequest

        change = session.get(ChangeRequest, change_id)
        if not change:
            raise typer.BadParameter("Unknown change request")
        job = queue_executor_job(
            session,
            client_id=change.client_id,
            kind="change_platform_validate",
            change_id=change.id,
            requested_by=current_actor_id(),
        )
        typer.echo(json.dumps({"job_id": job.id, "status": job.status, "kind": job.kind}, indent=2))


@app.command("change-client-approve")
def change_client_approve(change_id: str, approval_reference: str, client_approver: str) -> None:
    _admin()
    verify_db_schema()
    with session_scope() as session:
        request = record_client_approval(session, change_id, approval_reference, client_approver, current_actor_id())
        typer.echo(json.dumps({"id": request.id, "status": request.status, "payload_sha256": request.payload_sha256}, indent=2))


@app.command("change-admin-approve")
def change_admin_approve(change_id: str, payload_sha256: str) -> None:
    _admin()
    verify_db_schema()
    with session_scope() as session:
        request = admin_approve_change(session, change_id, payload_sha256, current_actor_id())
        typer.echo(json.dumps({"id": request.id, "status": request.status, "approved_by": request.admin_approved_by}, indent=2))


@app.command("change-apply-paused")
def change_apply_paused(change_id: str) -> None:
    """Queue only an approved paused build. Activation is intentionally absent from Eve."""
    _admin()
    verify_db_schema()
    with session_scope() as session:
        from .models import ChangeRequest

        change = session.get(ChangeRequest, change_id)
        if not change:
            raise typer.BadParameter("Unknown change request")
        job = queue_executor_job(
            session,
            client_id=change.client_id,
            kind="change_apply_paused",
            change_id=change.id,
            requested_by=current_actor_id(),
        )
        typer.echo(json.dumps({"job_id": job.id, "status": job.status, "kind": job.kind}, indent=2))


@app.command("ads-account-verify")
def ads_account_verify(client_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        job = queue_executor_job(
            session,
            client_id=client_id,
            kind="account_verify",
            requested_by=current_actor_id(),
        )
        typer.echo(json.dumps({"job_id": job.id, "status": job.status, "kind": job.kind}, indent=2))


@app.command("insights-sync")
def insights_sync(
    client_id: str,
    entity_external_id: str,
    period: str,
    aggregation_level: str = "campaign",
    timezone: str | None = None,
) -> None:
    verify_db_schema()
    with session_scope() as session:
        job = queue_executor_job(
            session,
            client_id=client_id,
            kind="insights_sync",
            requested_by=current_actor_id(),
            request={"entity_external_id": entity_external_id, "aggregation_level": aggregation_level, "period": period, "timezone": timezone, "requested_fields": []},
        )
        typer.echo(json.dumps({"job_id": job.id, "status": job.status, "kind": job.kind}, indent=2))


@app.command("executor-jobs")
def executor_jobs(client_id: str, limit: int = 20) -> None:
    """Admin-only inspection of the private executor queue; it never returns credentials."""
    _admin()
    verify_db_schema()
    with session_scope() as session:
        rows = list_executor_jobs(session, client_id, limit=limit)
        typer.echo(json.dumps([{"id": row.id, "kind": row.kind, "status": row.status, "change_id": row.change_id, "attempts": row.attempts, "result": row.result, "error": row.error} for row in rows], indent=2, default=str))


def _require_executor_process() -> None:
    if settings.process_role != "executor":
        raise typer.BadParameter("Executor commands require EVE_PROCESS_ROLE=executor in the private worker environment")


@app.command("executor-once")
def executor_once() -> None:
    """Run one queued key-requiring action from the private executor environment only."""
    _require_executor_process()
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(run_executor_once(session, current_actor_id()), indent=2, default=str))


@app.command("executor-drain")
def executor_drain(limit: int = 10) -> None:
    """Run a bounded queue drain from a private scheduler/Container App Job only."""
    _require_executor_process()
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(run_pending_executor_jobs(session, current_actor_id(), limit=limit), indent=2, default=str))


@app.command("executor-worker")
def executor_worker(poll_interval_seconds: float = 5.0, batch_size: int = 10) -> None:
    """Continuously poll the durable queue from a private, non-MCP worker process."""
    _require_executor_process()
    if poll_interval_seconds < 1 or poll_interval_seconds > 300:
        raise typer.BadParameter("poll_interval_seconds must be between 1 and 300")
    if batch_size < 1 or batch_size > 100:
        raise typer.BadParameter("batch_size must be between 1 and 100")
    verify_db_schema()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
    typer.echo("Private Eve executor worker started; SIGTERM or Ctrl-C stops it after the current batch.")
    while not stopping.is_set():
        with session_scope() as session:
            outcomes = run_pending_executor_jobs(session, current_actor_id(), limit=batch_size)
        if outcomes:
            typer.echo(json.dumps(outcomes, default=str))
        if len(outcomes) < batch_size:
            stopping.wait(poll_interval_seconds)
    typer.echo("Private Eve executor worker stopped.")


@app.command("ads-report")
def ads_report(client_id: str, format: str = "markdown") -> None:
    """Persist a report draft limited to Ads snapshots, evidence, and controlled comparisons."""
    verify_db_schema()
    with session_scope() as session:
        report = create_ads_report(session, client_id, current_actor_id())
        if format == "json":
            typer.echo(json.dumps({"id": report.id, "content_sha256": report.content_sha256, "readiness_status": report.readiness_status, "review_status": report.review_status, "report": report.report_json, "markdown": report.markdown}, indent=2, default=str))
        elif format == "markdown":
            typer.echo(f"Report ID: {report.id}\nContent SHA-256: {report.content_sha256}\nReadiness: {report.readiness_status}\nReview: {report.review_status}\n")
            typer.echo(report.markdown)
        else:
            raise typer.BadParameter("format must be markdown or json")


@app.command("ads-report-review")
def ads_report_review(report_id: str, decision: str, notes: str = "") -> None:
    """Admin human gate for a persisted report; approval requires blocker-free readiness."""
    verify_db_schema()
    with session_scope() as session:
        report = review_ads_report(session, report_id, decision, current_actor_id(), notes)
        typer.echo(json.dumps({"id": report.id, "content_sha256": report.content_sha256, "review_status": report.review_status, "reviewed_by": report.reviewed_by}, indent=2))


@app.command("ads-report-mark-sent")
def ads_report_mark_sent(report_id: str, reference: str) -> None:
    """Record that a client-ready report was shared; this command sends nothing."""
    verify_db_schema()
    with session_scope() as session:
        report = mark_ads_report_sent(session, report_id, reference, current_actor_id())
        typer.echo(json.dumps({"id": report.id, "content_sha256": report.content_sha256, "sent_by": report.sent_by, "sent_at": report.sent_at.isoformat() if report.sent_at else None}, indent=2))


@app.command("experiment-register")
def experiment_register(client_id: str, specification_json: str) -> None:
    """Persist a pre-registered two-arm controlled comparison from one JSON object."""
    verify_db_schema()
    specification = _json(specification_json, "specification_json")
    if not isinstance(specification, dict):
        raise typer.BadParameter("specification_json must be an object")
    with session_scope() as session:
        experiment = create_controlled_experiment(session, client_id, actor_id=current_actor_id(), **specification)
        typer.echo(json.dumps({"id": experiment.id, "status": experiment.status}, indent=2))


@app.command("experiments")
def experiments(client_id: str) -> None:
    verify_db_schema()
    with session_scope() as session:
        rows = session.scalars(select(ControlledExperiment).where(ControlledExperiment.client_id == client_id).order_by(ControlledExperiment.created_at.desc())).all()
        typer.echo(json.dumps([{"id": row.id, "name": row.name, "status": row.status, "primary_metric": row.primary_metric, "arms": row.arms, "result": row.result} for row in rows], indent=2, default=str))


@app.command("experiment-evaluate")
def experiment_evaluate(experiment_id: str, snapshot_ids_json: str) -> None:
    verify_db_schema()
    snapshot_ids = _json(snapshot_ids_json, "snapshot_ids_json")
    if not isinstance(snapshot_ids, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in snapshot_ids.items()):
        raise typer.BadParameter("snapshot_ids_json must map each arm name to one snapshot ID")
    with session_scope() as session:
        if not session.get(ControlledExperiment, experiment_id):
            raise typer.BadParameter("Unknown experiment")
        result = evaluate_controlled_experiment(session, experiment_id, snapshot_ids, actor_id=current_actor_id())
        typer.echo(json.dumps(result, indent=2, default=str))


@app.command("experiment-decide")
def experiment_decide(experiment_id: str, decision: str, approval_reference: str, rationale: str) -> None:
    """Admin-only terminal decision record after explicit interactive confirmation."""
    _admin()
    if not typer.confirm(f"Record HUMAN decision '{decision}' for experiment {experiment_id} with approval reference {approval_reference!r}?", default=False):
        typer.echo("Cancelled")
        raise typer.Exit(code=1)
    verify_db_schema()
    with session_scope() as session:
        experiment = record_controlled_experiment_decision(
            session, experiment_id, decision, approval_reference, rationale, actor_id=current_actor_id()
        )
        typer.echo(json.dumps({"id": experiment.id, "status": experiment.status, "result": experiment.result}, indent=2, default=str))


@app.command("secrets-keygen")
def secrets_keygen() -> None:
    """Generate a local-development encryption key; Azure uses Key Vault in shared deployment."""
    typer.echo(generate_master_key())


@app.command("secret-set")
def secret_set(client_id: str, name: str) -> None:
    """Masked secret intake; shared Azure sends directly to the private admin route."""
    remote_intake = settings.secret_backend == "azure_key_vault"
    if not remote_intake:
        _admin()
    else:
        validate_remote_secret_destination(settings.secret_intake_url, settings.secret_intake_scope)
    value = getpass(f"Enter value for {name}: ")
    if not value:
        raise typer.BadParameter("Secret cannot be empty")
    if remote_intake:
        try:
            vault_secret_name = write_key_vault_client_secret(client_id, name, value)
        except Exception:
            raise RuntimeError("Key Vault write failed; details withheld") from None
        try:
            result = remote_secret_request(
                url=settings.secret_intake_url,
                scope=settings.secret_intake_scope,
                method="POST",
                payload={"client_id": client_id, "name": name, "vault_secret_name": vault_secret_name},
            )
        except Exception:
            raise RuntimeError("Key Vault write succeeded but Eve metadata registration failed; retry the command") from None
        if result.get("stored") is not True or result.get("client_id") != client_id or result.get("name") != name:
            raise RuntimeError("Key Vault write succeeded but Eve metadata registration failed; retry the command")
        typer.echo(json.dumps({"client_id": client_id, "name": name, "stored": True, "backend": "azure_key_vault"}))
        return
    verify_db_schema()
    with session_scope() as session:
        set_client_secret(session, client_id, name, value, actor_id=current_actor_id())
    typer.echo(json.dumps({"client_id": client_id, "name": name, "stored": True}))


@app.command("secret-list")
def secret_list(client_id: str) -> None:
    if settings.secret_backend == "azure_key_vault":
        result = remote_secret_request(
            url=settings.secret_intake_url,
            scope=settings.secret_intake_scope,
            method="GET",
            query={"client_id": client_id},
        )
        if result.get("client_id") != client_id or not isinstance(result.get("secrets"), list):
            raise RuntimeError("Secret intake returned an unexpected response")
        typer.echo(json.dumps(result["secrets"], indent=2))
        return
    _admin()
    verify_db_schema()
    with session_scope() as session:
        typer.echo(json.dumps(list_client_secret_names(session, client_id), indent=2))


@app.command("secret-delete")
def secret_delete(client_id: str, name: str) -> None:
    """Admin-only confirmed removal from Eve's store; does not revoke the upstream Ads key."""
    remote_intake = settings.secret_backend == "azure_key_vault"
    if remote_intake:
        validate_remote_secret_destination(settings.secret_intake_url, settings.secret_intake_scope)
    if not typer.confirm(f"Remove stored {name} for client {client_id}? This does not revoke it in Ads Manager"):
        typer.echo("Cancelled")
        raise typer.Exit(code=1)
    if remote_intake:
        try:
            delete_key_vault_client_secret(client_id, name)
        except Exception:
            raise RuntimeError("Key Vault soft-delete failed; details withheld") from None
        try:
            result = remote_secret_request(
                url=settings.secret_intake_url,
                scope=settings.secret_intake_scope,
                method="DELETE",
                query={"client_id": client_id, "name": name},
            )
        except Exception:
            raise RuntimeError("Key Vault soft-delete succeeded but Eve metadata cleanup failed; retry the command") from None
        if result.get("client_id") != client_id or result.get("name") != name:
            raise RuntimeError("Secret intake returned an unexpected response")
        typer.echo(json.dumps({"client_id": client_id, "name": name, "deleted": result.get("deleted", False), "upstream_key_revoked": False}))
        return
    _admin()
    verify_db_schema()
    with session_scope() as session:
        deleted = delete_client_secret(session, client_id, name, actor_id=current_actor_id())
    typer.echo(json.dumps({"client_id": client_id, "name": name, "deleted": deleted, "upstream_key_revoked": False}))
