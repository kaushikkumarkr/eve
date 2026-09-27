"""Private executor for the few Eve actions that need an Ads credential.

The MCP/control-plane process only creates durable job records.  This module is
run in a separate worker identity which is the sole workload allowed to read a
client Ads credential from Azure Key Vault in shared deployment.  Jobs are
small, explicit and replayable; there is deliberately no arbitrary API-call
job type.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .control_plane import (
    _audit,
    apply_approved_paused_change,
    get_ads_workspace,
    poll_change_platform_validation,
    poll_paused_build,
    sync_campaign_insights,
    sync_entity_insights,
    validate_change_with_platform,
    verify_ads_workspace,
)
from .errors import safe_error_summary
from .models import AdsExecutorJob, ChangeRequest, utc_now


EXECUTOR_KINDS = {
    "account_verify",
    "change_platform_validate",
    "change_platform_poll",
    "change_apply_paused",
    "change_apply_poll",
    "insights_sync",
}
ACTIVE_STATUSES = {"queued", "running"}
LEASE_SECONDS = 300


def queue_executor_job(
    session: Session,
    *,
    client_id: str,
    kind: str,
    requested_by: str,
    change_id: str | None = None,
    request: dict[str, Any] | None = None,
) -> AdsExecutorJob:
    """Queue one explicit credential-requiring action without accessing secrets."""
    if kind not in EXECUTOR_KINDS:
        raise ValueError(f"Unsupported executor job kind: {kind}")
    request = request or {}
    if kind in {"change_platform_validate", "change_platform_poll", "change_apply_paused", "change_apply_poll"}:
        if not change_id:
            raise ValueError(f"{kind} requires a change_id")
        change = session.get(ChangeRequest, change_id)
        if not change or change.client_id != client_id:
            raise ValueError("Change request does not belong to this client")
        if kind == "change_platform_validate" and change.status not in {"proposed", "platform_validation_failed"}:
            raise ValueError("Only a proposed or previously failed change can be submitted for platform validation")
        if kind == "change_platform_poll" and change.status != "platform_validation_pending":
            raise ValueError("Only a pending platform validation can be polled")
        if kind == "change_apply_paused" and change.status != "admin_approved":
            raise ValueError("Only an admin-approved change can be queued for a paused apply")
        if kind == "change_apply_poll" and change.status != "build_pending":
            raise ValueError("Only a pending paused build can be polled")
    elif change_id:
        raise ValueError(f"{kind} must not include a change_id")

    if kind == "insights_sync":
        required = {"entity_external_id", "period"}
        if missing := sorted(key for key in required if not isinstance(request.get(key), str) or not request[key].strip()):
            raise ValueError(f"insights_sync request is missing: {missing}")
        if request.get("aggregation_level", "campaign") not in {"campaign", "ad_group", "ad"}:
            raise ValueError("insights_sync aggregation_level must be campaign, ad_group, or ad")
    if kind in {"account_verify", "insights_sync"}:
        get_ads_workspace(session, client_id)

    duplicate = session.scalar(
        select(AdsExecutorJob).where(
            AdsExecutorJob.client_id == client_id,
            AdsExecutorJob.kind == kind,
            AdsExecutorJob.change_id == change_id,
            AdsExecutorJob.status.in_(ACTIVE_STATUSES),
        )
    )
    if duplicate:
        return duplicate

    job = AdsExecutorJob(
        client_id=client_id,
        change_id=change_id,
        kind=kind,
        request=request,
        requested_by=requested_by,
    )
    session.add(job)
    session.flush()
    _audit(
        session,
        action="executor_job.queued",
        entity_type="ads_executor_job",
        entity_id=job.id,
        client_id=client_id,
        actor_id=requested_by,
        payload={"kind": kind, "change_id": change_id},
    )
    session.commit()
    return job


def list_executor_jobs(session: Session, client_id: str, *, limit: int = 20) -> list[AdsExecutorJob]:
    return session.scalars(
        select(AdsExecutorJob)
        .where(AdsExecutorJob.client_id == client_id)
        .order_by(AdsExecutorJob.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()


def _claim_next_job(session: Session, worker_id: str) -> AdsExecutorJob | None:
    """Claim one job. PostgreSQL uses SKIP LOCKED; SQLite remains deterministic for tests."""
    expired = session.scalars(
        select(AdsExecutorJob).where(
            AdsExecutorJob.status == "running",
            AdsExecutorJob.lease_expires_at.is_not(None),
            AdsExecutorJob.lease_expires_at < utc_now(),
        )
    ).all()
    for stale in expired:
        stale.status = "queued"
        stale.claimed_by = None
        stale.lease_expires_at = None
        _audit(
            session,
            action="executor_job.requeued_after_lease_expiry",
            entity_type="ads_executor_job",
            entity_id=stale.id,
            client_id=stale.client_id,
            actor_id=worker_id,
            payload={"kind": stale.kind, "attempt": stale.attempts},
        )
    if expired:
        session.commit()
    statement = (
        select(AdsExecutorJob)
        .where(
            AdsExecutorJob.status == "queued",
            (AdsExecutorJob.not_before_at.is_(None)) | (AdsExecutorJob.not_before_at <= utc_now()),
        )
        .order_by(AdsExecutorJob.created_at)
        .limit(1)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    job = session.scalar(statement)
    if not job:
        return None
    job.status = "running"
    job.attempts += 1
    job.claimed_by = worker_id
    job.lease_expires_at = utc_now() + timedelta(seconds=LEASE_SECONDS)
    job.started_at = utc_now()
    session.flush()
    _audit(
        session,
        action="executor_job.claimed",
        entity_type="ads_executor_job",
        entity_id=job.id,
        client_id=job.client_id,
        actor_id=worker_id,
        payload={"kind": job.kind, "attempt": job.attempts},
    )
    session.commit()
    return job


def _execute_job(session: Session, job: AdsExecutorJob, worker_id: str) -> dict[str, Any]:
    if job.kind == "account_verify":
        return verify_ads_workspace(session, job.client_id, actor_id=worker_id)
    if job.kind == "change_platform_validate":
        assert job.change_id
        return validate_change_with_platform(session, job.change_id, actor_id=worker_id)
    if job.kind == "change_platform_poll":
        assert job.change_id
        return poll_change_platform_validation(session, job.change_id, actor_id=worker_id)
    if job.kind == "change_apply_paused":
        assert job.change_id
        return apply_approved_paused_change(session, job.change_id, actor_id=worker_id)
    if job.kind == "change_apply_poll":
        assert job.change_id
        return poll_paused_build(session, job.change_id, actor_id=worker_id)
    if job.kind == "insights_sync":
        request = job.request or {}
        return sync_entity_insights(
            session,
            job.client_id,
            request["entity_external_id"],
            aggregation_level=request.get("aggregation_level", "campaign"),
            period=request["period"],
            timezone=request.get("timezone"),
            requested_fields=request.get("requested_fields"),
            actor_id=worker_id,
        )
    raise ValueError(f"Unsupported executor job kind: {job.kind}")


def run_executor_once(session: Session, worker_id: str) -> dict[str, Any]:
    """Execute one queued job; worker invocation is CLI/private-scheduler only."""
    job = _claim_next_job(session, worker_id)
    if not job:
        return {"status": "idle"}
    try:
        result = _execute_job(session, job, worker_id)
        refreshed = session.get(AdsExecutorJob, job.id)
        assert refreshed is not None
        if job.kind == "change_platform_validate" and result.get("pending"):
            queue_executor_job(
                session,
                client_id=job.client_id,
                kind="change_platform_poll",
                change_id=job.change_id,
                requested_by=worker_id,
            )
        if job.kind == "change_apply_paused" and result.get("pending"):
            queue_executor_job(
                session,
                client_id=job.client_id,
                kind="change_apply_poll",
                change_id=job.change_id,
                requested_by=worker_id,
            )
        if job.kind in {"change_platform_poll", "change_apply_poll"} and result.get("pending"):
            refreshed.status = "queued"
            refreshed.result = result
            refreshed.error = None
            refreshed.claimed_by = None
            refreshed.lease_expires_at = None
            refreshed.not_before_at = utc_now() + timedelta(seconds=10)
            _audit(
                session,
                action="executor_job.deferred",
                entity_type="ads_executor_job",
                entity_id=refreshed.id,
                client_id=refreshed.client_id,
                actor_id=worker_id,
                payload={"kind": refreshed.kind},
            )
            session.commit()
            return {"job_id": refreshed.id, "status": "deferred", "result": result}
        refreshed.status = "completed"
        refreshed.result = result
        refreshed.error = None
        refreshed.completed_at = utc_now()
        refreshed.lease_expires_at = None
        _audit(
            session,
            action="executor_job.completed",
            entity_type="ads_executor_job",
            entity_id=refreshed.id,
            client_id=refreshed.client_id,
            actor_id=worker_id,
            payload={"kind": refreshed.kind},
        )
        session.commit()
        return {"job_id": refreshed.id, "status": refreshed.status, "result": result}
    except Exception as exc:
        session.rollback()
        failed = session.get(AdsExecutorJob, job.id)
        safe_error = safe_error_summary(exc)
        if failed:
            failed.status = "failed"
            failed.error = safe_error
            failed.completed_at = utc_now()
            failed.lease_expires_at = None
            _audit(
                session,
                action="executor_job.failed",
                entity_type="ads_executor_job",
                entity_id=failed.id,
                client_id=failed.client_id,
                actor_id=worker_id,
                payload={"kind": failed.kind, "error_type": type(exc).__name__},
            )
            session.commit()
        # Do not let a provider's response body or exception message reach the
        # CLI/container logs. The durable job has the safe diagnostic above.
        raise RuntimeError(safe_error) from None


def run_pending_executor_jobs(session: Session, worker_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Drain a bounded number of jobs for a private scheduler or Container App Job."""
    outcomes: list[dict[str, Any]] = []
    for _ in range(max(1, min(limit, 100))):
        outcome = run_executor_once(session, worker_id)
        if outcome["status"] in {"idle", "deferred"}:
            break
        outcomes.append(outcome)
    return outcomes
