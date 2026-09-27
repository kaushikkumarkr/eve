"""Small, durable workflow records without an internal agent orchestration layer.

Codex or Claude Code is the interactive orchestrator. Eve persists only the
deterministic work that benefits from a durable job record: readiness checks and
local blueprint compilation. It deliberately does not simulate conversations,
invent visibility gaps, or make campaign choices on behalf of an operator.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .control_plane import compile_blueprint, workspace_summary
from .errors import safe_error_summary
from .models import CampaignBlueprint, Client, Evidence, SourceDocument, WorkflowJob


def _start_job(session: Session, client_id: str, kind: str) -> WorkflowJob:
    job = WorkflowJob(client_id=client_id, kind=kind, status="running", state={})
    session.add(job)
    session.commit()
    return job


def _finish_job(session: Session, job: WorkflowJob, result: dict[str, Any]) -> dict[str, Any]:
    job.status = "completed"
    job.state = result
    session.commit()
    return {"job_id": job.id, "status": job.status, **result}


def _fail_job(session: Session, job: WorkflowJob, exc: Exception) -> None:
    job.status = "failed"
    job.error = safe_error_summary(exc)
    session.commit()


def run_workspace_readiness(session: Session, client_id: str) -> dict[str, Any]:
    """Record deterministic operational blockers; no research or external mutation."""
    client = session.get(Client, client_id)
    if not client:
        raise ValueError(f"Unknown client: {client_id}")
    job = _start_job(session, client_id, "workspace_readiness")
    try:
        sources = session.scalars(select(SourceDocument).where(SourceDocument.client_id == client_id)).all()
        evidence = session.scalars(select(Evidence).where(Evidence.client_id == client_id)).all()
        blockers: list[str] = []
        profile = client.profile or {}
        required_profile = ["offer", "audience", "goals"]
        missing_profile = [key for key in required_profile if not profile.get(key)]
        if missing_profile:
            blockers.append(f"client profile is missing: {', '.join(missing_profile)}")
        if not sources:
            blockers.append("no authorized source material has been ingested")
        if evidence and not any(item.review_status == "approved" for item in evidence):
            blockers.append("no evidence item has been human-reviewed")
        try:
            summary = workspace_summary(session, client_id)
        except ValueError:
            summary = None
            blockers.append("Ads workspace is not configured")
        result = {
            "ready_for_drafting": not missing_profile and bool(sources),
            "ready_for_client_facing_claims": not blockers,
            "blockers": blockers,
            "workspace": summary,
            "note": "No visibility simulation or organic-ranking claim was created.",
        }
        return _finish_job(session, job, result)
    except Exception as exc:
        _fail_job(session, job, exc)
        raise RuntimeError(safe_error_summary(exc)) from None


def run_blueprint_preflight(session: Session, blueprint_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Persist the local compilation/check as a durable job for hand-off."""
    blueprint = session.get(CampaignBlueprint, blueprint_id)
    if not blueprint:
        raise ValueError(f"Unknown campaign blueprint: {blueprint_id}")
    job = _start_job(session, blueprint.client_id, "blueprint_preflight")
    try:
        result = compile_blueprint(session, blueprint_id, actor_id)
        return _finish_job(session, job, result)
    except Exception as exc:
        _fail_job(session, job, exc)
        raise RuntimeError(safe_error_summary(exc)) from None
