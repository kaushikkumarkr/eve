from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import agency_mcp.ads as ads_module
from agency_mcp.control_plane import configure_ads_workspace
from agency_mcp.db import _alembic_config
from agency_mcp.executor import queue_executor_job, run_executor_once
from agency_mcp.models import AdsExecutorJob
from agency_mcp.reporting import create_ads_report, mark_ads_report_sent, review_ads_report
from agency_mcp.service import create_client, ingest_text, review_evidence


@pytest.mark.skipif(not os.getenv("EVE_TEST_POSTGRES_URL"), reason="requires a dedicated PostgreSQL test database")
def test_postgres_executor_queue_persists_a_mock_verification(monkeypatch):
    """Exercise JSON persistence and executor queue state on the shared DB engine."""
    engine = create_engine(os.environ["EVE_TEST_POSTGRES_URL"], pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            database_name = connection.scalar(text("select current_database()"))
            if not database_name.endswith("_test"):
                raise AssertionError("EVE_TEST_POSTGRES_URL must target a database ending in _test")
            revision = connection.scalar(text("select version_num from alembic_version"))
            expected_head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
            if revision != expected_head:
                raise AssertionError(
                    f"PostgreSQL test database must be migrated to head {expected_head!r}; got {revision!r}"
                )

        monkeypatch.setattr(ads_module, "settings", replace(ads_module.settings, ads_mode="mock"))
        Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        suffix = uuid4().hex[:10]
        with Session() as session:
            client = create_client(session, f"Postgres integration {suffix}", "digital_products")
            configure_ads_workspace(session, client.id, ad_account_id="mock_account")
            job = queue_executor_job(
                session,
                client_id=client.id,
                kind="account_verify",
                requested_by="postgres-integration-test",
            )
            job_id = job.id

        with Session() as session:
            result = run_executor_once(session, "postgres-integration-worker")
            persisted = session.get(AdsExecutorJob, job_id)
            assert result["status"] == "completed"
            assert persisted is not None
            assert persisted.status == "completed"
            assert persisted.result["status"] == "verified"

            source = ingest_text(
                session,
                persisted.client_id,
                "integration evidence",
                "Operators compare workflow tools before changing process.",
            )
            review_evidence(session, source.evidence[0].id, "approved", "postgres-reviewer", persisted.client_id)
            report = create_ads_report(session, persisted.client_id, "postgres-operator")
            assert report.readiness_status == "reviewable"
            assert report.review_status == "pending_review"
            reviewed = review_ads_report(session, report.id, "approve", "postgres-admin")
            assert reviewed.review_status == "client_ready"
            sent = mark_ads_report_sent(session, report.id, "postgres integration event", "postgres-admin")
            assert sent.sent_at is not None
    finally:
        engine.dispose()
