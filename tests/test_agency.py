from __future__ import annotations

import json
from dataclasses import replace
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agency_mcp.db import Base
from agency_mcp.models import AuditLog, CampaignPlan, Opportunity, SourceDocument
from agency_mcp.ads import GuardedRealAdsAdapter, MockAdsAdapter
import agency_mcp.ads as ads_module
from agency_mcp.reporting import build_client_report, report_markdown
from agency_mcp.connectors import ingest_crm_csv
from agency_mcp.measurement import record_conversion, register_conversion_source
from agency_mcp.measurement import check_conversion_batch
import agency_mcp.secret_store as secret_store
from agency_mcp.secret_store import generate_master_key, get_client_secret, list_client_secret_names, set_client_secret
from agency_mcp.policy import check_advertising_policy
from agency_mcp.service import sync_insights
from agency_mcp.service import apply_approved_campaign, approve, request_approval
from agency_mcp.service import (
    create_campaign_plan,
    create_client,
    generate_context_hints,
    ingest_text,
    preview_campaign,
    validate_campaign_plan,
)
from agency_mcp.workflows import run_research
from agency_mcp.workflows import run_campaign
from agency_mcp.config import normalize_azure_endpoint


def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_end_to_end_research_and_campaign_preview(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Synthetic Trail Co", "consumer_goods", "https://example.com")
        ingest_text(
            session,
            client.id,
            "voc.txt",
            "Buyers compare quality and price. They need a reliable option and want delivery timing before switching brands.",
            "synthetic",
        )
        result = run_research(session, client.id)
        assert result["status"] == "gaps_identified"
        assert result["opportunity_ids"]

        hints = generate_context_hints(session, result["opportunity_ids"][0])
        assert hints[0].status == "draft"

        plan = create_campaign_plan(session, result["opportunity_ids"][0])
        validation = validate_campaign_plan(session, plan.id)
        assert validation["valid"] is True

        preview = preview_campaign(session, plan.id)
        assert preview["approval_required"] is True
        assert preview["spend"] == 0
        assert preview["ads_preview"]["dry_run"] is True
        assert preview["ads_preview"]["would_apply"]["ad_group"]["context_hints"]
        assert preview["ads_preview"]["would_apply"]["campaign"]["budget"]["requires_client_approval"] is True
        assert preview["ads_preview"]["would_apply"]["ad"]["name"]
        assert len(hints) == 3

        logs = session.scalars(select(AuditLog)).all()
        assert len(logs) >= 8


def test_invalid_creative_is_rejected(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Bad Creative Co", "general")
        ingest_text(session, client.id, "voc.txt", "Customers compare quality and price.")
        run_research(session, client.id)
        opportunity = session.scalars(select(Opportunity).where(Opportunity.client_id == client.id)).first()
        plan = create_campaign_plan(session, opportunity.id)
        plan.creative = {"title": "x", "body": "Fine", "target_url": "ftp://bad.example"}
        session.commit()
        result = validate_campaign_plan(session, plan.id)
        assert result["valid"] is False
        assert len(result["errors"]) == 2


def test_report_is_grounded_and_mock_ads_never_spend(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Report Test", "education")
        ingest_text(session, client.id, "voc.txt", "Students compare quality and price.")
        run_research(session, client.id)
        report = build_client_report(session, client.id)
        markdown = report_markdown(report)
        assert "simulated hypothesis" in markdown
        assert report["counts"]["opportunities"] > 0
        mock_result = MockAdsAdapter().apply_mutation({"spend": 1}, "tester")
        assert mock_result["mode"] == "mock"
        assert mock_result["spend"] == 0


def test_crm_ingest_excludes_direct_identifiers(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "CRM Test", "b2b")
        result = ingest_crm_csv(
            session,
            client.id,
            "hubspot",
            "export.csv",
            "email,company,role,notes\nuser@example.com,Acme,Founder,Needs a reliable alternative\n",
        )
        assert result.rows_read == 1
        assert "email" in result.excluded_columns
        # The raw email is excluded from the stored text by the connector contract.
        from agency_mcp.models import SourceDocument

        stored = session.get(SourceDocument, result.source_id)
        assert "user@example.com" not in stored.content
        assert "Acme" in stored.content


def test_policy_preflight_and_conversion_deduplication(tmp_path):
    assert check_advertising_policy("consumer_goods", "A useful product", "Clear value", "https://example.com").status == "allowed_preflight"
    assert check_advertising_policy("healthcare", "Medical service", "Learn more", "https://example.com").status == "manual_review"
    assert check_advertising_policy("gambling", "Win big", "Guaranteed return", "https://example.com").status == "rejected"

    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Measurement Test", "education")
        source = register_conversion_source(session, client.id, "pixel", "pix_test", "Test Pixel")
        first = record_conversion(session, client.id, source.id, "evt-1", "lead_created")
        second = record_conversion(session, client.id, source.id, "evt-1", "lead_created")
        assert first["accepted"] is True
        assert second["deduplicated"] is True
        insights = sync_insights(session, client.id, "mock-campaign")
        assert insights["metrics"]["spend"] == 0.0


def test_approval_gate_allows_only_paused_zero_spend_mock_apply(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Approval Test", "education", "https://example.com")
        ingest_text(session, client.id, "voc.txt", "Learners compare quality and price.")
        result = run_research(session, client.id)
        plan = create_campaign_plan(session, result["opportunity_ids"][0])
        approval = request_approval(session, "campaign_plan", plan.id, "apply")
        try:
            apply_approved_campaign(session, plan.id, approval.id, "tester")
        except ValueError as exc:
            assert "approved approval" in str(exc)
        else:
            raise AssertionError("unapproved campaign must not apply")
        approve(session, approval.id, "tester")
        applied = apply_approved_campaign(session, plan.id, approval.id, "tester")
        assert applied["status"] == "submitted"
        assert applied["spend"] == 0
        assert applied["result"]["applied"] is True
        assert applied["result"]["payload"]["campaign"]["status"] == "paused"


def test_real_ads_adapter_contract_is_gated_and_idempotent(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout):
        calls.append(request)
        if request.full_url.endswith("/campaigns"):
            return FakeResponse({"id": "cmp_test"})
        if request.full_url.endswith("/ad_groups"):
            return FakeResponse({"id": "grp_test"})
        return FakeResponse({"id": "ad_test"})

    monkeypatch.setattr(ads_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(ads_module, "settings", replace(ads_module.settings, mutations_enabled=True))
    adapter = GuardedRealAdsAdapter("secret", "https://ads.example/v1")
    result = adapter.apply_mutation(
        {
            "campaign": {"name": "Test", "status": "active", "budget": {"daily_spend_limit_micros": 1000000}},
            "ad_group": {"name": "Test group", "status": "active", "bidding_config": {"max_bid_micros": 1000000}},
            "ad": {"status": "active", "creative": {"title": "Test"}},
        },
        "tester",
    )
    assert result["campaign"]["id"] == "cmp_test"
    assert len(calls) == 3
    assert all(json.loads(request.data.decode())["status"] == "paused" for request in calls)
    assert all(request.headers.get("Idempotency-key") for request in calls)


def test_campaign_workflow_is_langgraph_orchestrated(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Campaign Graph Test", "local_services", "https://example.com")
        ingest_text(session, client.id, "voc.txt", "Customers compare quality and price.")
        research = run_research(session, client.id)
        result = run_campaign(session, research["opportunity_ids"][0])
        assert result["status"] == "previewed"
        assert result["validation"]["valid"] is True
        assert result["preview"]["spend"] == 0


def test_normalize_azure_endpoint():
    assert normalize_azure_endpoint(
        "https://example.openai.azure.com/openai/deployments/demo"
    ) == "https://example.openai.azure.com"


def test_service_package_redacts_sources_and_tracks_jobs(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Service Package Test", "local_services")
        from agency_mcp.service import get_client_workspace, update_client_profile

        update_client_profile(
            session,
            client.id,
            {"audience": "local homeowners", "locations": ["New York"], "goals": ["qualified leads"]},
        )
        ingest_text(
            session,
            client.id,
            "sales-call.txt",
            "The buyer compares quality and price. Contact jane@example.com at 212-555-0198.",
            "call_transcript",
        )
        source = session.scalars(select(SourceDocument).where(SourceDocument.client_id == client.id)).one()
        assert "jane@example.com" not in source.content
        assert "[REDACTED_EMAIL]" in source.content
        assert source.content_hash
        assert source.redaction_summary["email"] == 1

        from agency_mcp.workflows import run_service_package

        result = run_service_package(session, client.id)
        assert result["status"] == "ready_for_review"
        assert result["spend"] == 0
        assert result["campaign"]["preview"]["spend"] == 0
        workspace = get_client_workspace(session, client.id)
        assert workspace["client"]["profile"]["audience"] == "local homeowners"
        assert workspace["counts"]["opportunities"] > 0
        assert {job["status"] for job in workspace["recent_jobs"]} == {"completed"}


def test_client_secrets_are_encrypted_and_conversion_batch_is_safe(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(
        secret_store,
        "settings",
        replace(secret_store.settings, agency_master_key=generate_master_key()),
    )
    with Session() as session:
        client = create_client(session, "Secret Test", "education")
        set_client_secret(session, client.id, "OPENAI_ADS_API_KEY", "ads-secret-value")
        stored = session.scalars(select(secret_store.ClientSecret)).one()
        assert stored.ciphertext != "ads-secret-value"
        assert get_client_secret(session, client.id, "OPENAI_ADS_API_KEY") == "ads-secret-value"
        assert list_client_secret_names(session, client.id)[0]["name"] == "OPENAI_ADS_API_KEY"

        result = check_conversion_batch(
            session,
            client.id,
            "pixel_test",
            [{
                "id": "evt-1",
                "type": "lead_created",
                "timestamp_ms": 1730000000000,
                "action_source": "web",
                "data": {"type": "customer_action"},
            }],
        )
        assert result["valid"] is True
        assert result["status"] == "locally_validated"

        invalid = check_conversion_batch(session, client.id, "pixel_test", [{}])
        assert invalid["valid"] is False
