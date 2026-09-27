from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

import agency_mcp.ads as ads_module
import agency_mcp.auth as auth_module
import agency_mcp.cli as cli_module
import agency_mcp.db as db_module
import agency_mcp.secret_store as secret_store
from agency_mcp.ads import AdsMutationBlocked, GuardedRealAdsAdapter, get_ads_adapter
from agency_mcp.auth import Auth0TokenVerifier, EntraTokenVerifier
from agency_mcp.control_plane import (
    admin_approve_change,
    apply_approved_paused_change,
    compile_blueprint,
    configure_ads_workspace,
    create_campaign_blueprint,
    create_controlled_experiment,
    create_hint_set,
    evaluate_controlled_experiment,
    grant_client_access,
    payload_sha256,
    poll_change_platform_validation,
    poll_paused_build,
    propose_build_change,
    record_client_approval,
    record_controlled_experiment_decision,
    sync_campaign_insights,
    sync_entity_insights,
    verify_audit_chain,
    validate_change_with_platform,
    workspace_summary,
)
from agency_mcp.db import Base
from agency_mcp.executor import queue_executor_job, run_executor_once
import agency_mcp.executor as executor_module
import agency_mcp.workflows as workflows_module
import agency_mcp.mcp_server as mcp_server
from agency_mcp.models import AdsExecutorJob, AdsInsightSnapshot, AuditLog, ChangeRequest, WorkflowJob
from agency_mcp.reporting import create_ads_report, mark_ads_report_sent, review_ads_report
from agency_mcp.secret_store import (
    delete_client_secret,
    generate_master_key,
    get_client_secret,
    list_client_secret_names,
    register_key_vault_secret_reference,
    set_client_secret,
    write_key_vault_client_secret,
)
from agency_mcp.service import create_client, ingest_text, review_evidence, update_client_profile
from agency_mcp.workflows import run_blueprint_preflight, run_workspace_readiness
from typer.testing import CliRunner


def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_service_schema_check_is_read_only_and_migrations_are_explicit(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'schema-gate.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "settings", replace(db_module.settings, database_url=database_url))
    with pytest.raises(RuntimeError, match="agency db-upgrade"):
        db_module.verify_db_schema()
    assert inspect(engine).get_table_names() == []

    db_module.migrate_db()
    db_module.verify_db_schema()
    engine.dispose()


def test_entra_verifier_binds_identity_tenant_audience_and_app_role():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": public_key})()

    verifier = EntraTokenVerifier("tenant-123", "eve-app-id", "https://eve.example/mcp")
    verifier._jwks = FakeJwks()

    def token_for(**overrides):
        claims = {
            "iss": "https://login.microsoftonline.com/tenant-123/v2.0",
            "aud": "eve-app-id",
            "tid": "tenant-123",
            "oid": "operator-456",
            "scp": "https://eve.example/mcp/operator",
            "roles": ["Eve.Operator"],
            "iat": 1700000000,
            "exp": 2000000000,
        }
        claims.update(overrides)
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})

    valid = asyncio.run(verifier.verify_token(token_for()))
    assert valid is not None
    assert valid.client_id == "tenant-123:operator-456"
    assert valid.scopes == ["https://eve.example/mcp/operator"]

    admin = asyncio.run(verifier.verify_token(token_for(roles=["Eve.Admin"])))
    assert admin is not None
    assert admin.scopes == ["https://eve.example/mcp/operator", "admin"]

    assert asyncio.run(verifier.verify_token(token_for(tid="another-tenant"))) is None
    assert asyncio.run(verifier.verify_token(token_for(roles=["Eve.ReadOnly"]))) is None
    assert asyncio.run(verifier.verify_token(token_for(scp=""))) is None
    assert asyncio.run(verifier.verify_token(token_for(aud="wrong-audience"))) is None


def test_auth0_verifier_binds_issuer_audience_scope_and_eve_role():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": public_key})()

    role_claim = "https://eve.internal/roles"
    resource = "https://eve.example/mcp"
    verifier = Auth0TokenVerifier("example.us.auth0.com", "https://eve.example/mcp", resource, role_claim)
    verifier._jwks = FakeJwks()

    def token_for(**overrides):
        claims = {
            "iss": "https://example.us.auth0.com/",
            "aud": "https://eve.example/mcp",
            "sub": "waad|operator-456",
            "scope": "operator",
            role_claim: ["Eve.Operator"],
            "iat": 1700000000,
            "exp": 2000000000,
        }
        claims.update(overrides)
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})

    valid = asyncio.run(verifier.verify_token(token_for()))
    assert valid is not None
    assert valid.client_id == "auth0:waad|operator-456"
    assert valid.scopes == ["operator"]

    admin = asyncio.run(verifier.verify_token(token_for(**{role_claim: ["Eve.Admin"]})))
    assert admin is not None
    assert admin.scopes == ["operator", "admin"]

    assert asyncio.run(verifier.verify_token(token_for(iss="https://other.auth0.com/"))) is None
    assert asyncio.run(verifier.verify_token(token_for(aud="wrong-audience"))) is None
    assert asyncio.run(verifier.verify_token(token_for(scope="profile"))) is None
    assert asyncio.run(verifier.verify_token(token_for(**{role_claim: ["Eve.ReadOnly"]}))) is None
    assert asyncio.run(verifier.verify_token(token_for(**{role_claim: "Eve.Admin"}))) is None


def desired_state() -> dict:
    return {
        "campaign": {
            "name": "Acme US lead launch",
            "billing_event_type": "click",
            "budget_type": "daily",
            "max_budget_micros": 10_000_000,
            "target_countries": ["US"],
        },
        "ad_group": {
            "name": "Switching trigger",
            "strategy": "fixed_bid",
            "max_bid_micros": 1_500_000,
        },
        "ad": {
            "name": "Switching chat card",
            "title": "Explore Acme's workflow",
            "body": "See whether the workflow fits your team.",
            "target_url": "https://example.com/switching",
            "source_image_url": "https://example.com/assets/chat-card.png",
        },
    }


def make_draft(session):
    client = create_client(session, "Acme", "digital_products", "https://example.com")
    update_client_profile(
        session,
        client.id,
        {"offer": "Acme workflow", "audience": "operations teams", "goals": ["qualified demos"]},
    )
    source = ingest_text(
        session,
        client.id,
        "interview-notes.txt",
        "Operators need a clearer way to compare workflow tools before switching.",
        "interview_notes",
        source_url="https://example.com/research",
        retrieved_at="2026-09-26",
    )
    evidence = source.evidence[0]
    review_evidence(session, evidence.id, "approved", "admin", client.id)
    workspace = configure_ads_workspace(session, client.id, actor_id="operator")
    hint_set = create_hint_set(
        session,
        client.id,
        "switching-trigger",
        ["Workflow software for operations teams evaluating a switch after their current process becomes difficult to manage."],
        evidence_ids=[evidence.id],
        rationale="Directly grounded in approved interview notes.",
        actor_id="operator",
    )
    blueprint = create_campaign_blueprint(
        session,
        client.id,
        workspace.id,
        hint_set.id,
        "Acme switching test",
        desired_state(),
        actor_id="operator",
    )
    return client, workspace, hint_set, blueprint


def test_full_paused_build_lifecycle_is_hash_bound_and_zero_spend(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client, workspace, hint_set, blueprint = make_draft(session)
        compiled = compile_blueprint(session, blueprint.id, "operator")

        assert compiled["valid"] is True
        assert compiled["payload"]["validate_only"] is True
        assert all(operation["input"]["status"] == "paused" for operation in compiled["payload"]["operations"])
        assert compiled["payload"]["operations"][1]["input"]["context_hints"] == hint_set.hints
        assert compiled["payload_sha256"] == payload_sha256(compiled["payload"])

        change = propose_build_change(session, blueprint.id, "Approved foundation build", "operator")
        submitted = validate_change_with_platform(session, change.id, "operator")
        assert submitted["status"] == "platform_validation_pending"
        platform = poll_change_platform_validation(session, change.id, "operator")
        assert platform["status"] == "platform_validated"

        record_client_approval(session, change.id, "signed SOW #42", "Acme marketing lead", "admin")
        approved = admin_approve_change(session, change.id, change.payload_sha256, "admin")
        submitted_apply = apply_approved_paused_change(session, approved.id, "admin")
        assert submitted_apply["status"] == "build_pending"
        applied = poll_paused_build(session, approved.id, "admin")

        assert applied["status"] == "built_paused"
        assert all(operation["status"] == "created" for operation in applied["operations"])

        summary = workspace_summary(session, client.id)
        assert summary["counts"]["blueprints"] == 1
        assert summary["recent_changes"][0]["status"] == "built_paused"
        audit = session.scalars(select(AuditLog).where(AuditLog.client_id == client.id)).all()
        assert any(item.action == "change.paused_build_completed" and item.entry_hash for item in audit)


def test_private_executor_is_the_only_path_from_queued_action_to_ads_adapter(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client, _, _, blueprint = make_draft(session)
        change = propose_build_change(session, blueprint.id, "Validation through private worker", "operator")

        queued_validation = queue_executor_job(
            session,
            client_id=client.id,
            kind="change_platform_validate",
            change_id=change.id,
            requested_by="operator",
        )
        # Re-submitting an active job is idempotent at the control-plane layer.
        assert queue_executor_job(
            session,
            client_id=client.id,
            kind="change_platform_validate",
            change_id=change.id,
            requested_by="operator",
        ).id == queued_validation.id
        assert session.get(ChangeRequest, change.id).status == "proposed"

        validation_result = run_executor_once(session, "eve-executor")
        assert validation_result["status"] == "completed"
        assert session.get(AdsExecutorJob, queued_validation.id).status == "completed"
        poll_result = run_executor_once(session, "eve-executor")
        assert poll_result["status"] == "completed"
        assert session.get(ChangeRequest, change.id).status == "platform_validated"

        record_client_approval(session, change.id, "signed SOW #43", "Acme marketing lead", "admin")
        admin_approve_change(session, change.id, change.payload_sha256, "admin")
        queued_apply = queue_executor_job(
            session,
            client_id=client.id,
            kind="change_apply_paused",
            change_id=change.id,
            requested_by="admin",
        )
        apply_result = run_executor_once(session, "eve-executor")
        assert apply_result["status"] == "completed"
        assert session.get(AdsExecutorJob, queued_apply.id).status == "completed"
        apply_poll_result = run_executor_once(session, "eve-executor")
        assert apply_poll_result["status"] == "completed"
        assert session.get(ChangeRequest, change.id).status == "built_paused"


def test_executor_suppresses_provider_exception_text_from_persisted_and_raised_errors(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Failure redaction", "digital_products")
        configure_ads_workspace(session, client.id, ad_account_id="mock_account")
        job = queue_executor_job(
            session,
            client_id=client.id,
            kind="account_verify",
            requested_by="operator",
        )
        job_id = job.id

    provider_detail = "provider echoed credential OPENAI_ADS_API_KEY=sk-sensitive-test-value"

    def fail_with_provider_detail(*args, **kwargs):
        raise RuntimeError(provider_detail)

    monkeypatch.setattr(executor_module, "verify_ads_workspace", fail_with_provider_detail)
    with Session() as session:
        with pytest.raises(RuntimeError, match="details withheld") as raised:
            run_executor_once(session, "eve-executor")
        persisted = session.get(AdsExecutorJob, job_id)
        assert persisted is not None
        assert persisted.status == "failed"
        assert "RuntimeError" in persisted.error
        assert "sk-sensitive-test-value" not in persisted.error
        assert "sk-sensitive-test-value" not in str(raised.value)


def test_workflow_suppresses_sensitive_exception_text_from_job_and_caller(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    with Session() as session:
        _, _, _, blueprint = make_draft(session)
        blueprint_id = blueprint.id

    private_detail = "customer upload contained secret-token-abc123"

    def fail_with_private_detail(*args, **kwargs):
        raise ValueError(private_detail)

    monkeypatch.setattr(workflows_module, "compile_blueprint", fail_with_private_detail)
    with Session() as session:
        with pytest.raises(RuntimeError, match="details withheld") as raised:
            run_blueprint_preflight(session, blueprint_id, "operator")
        failed_job = session.scalar(select(WorkflowJob).where(WorkflowJob.client_id == blueprint.client_id))
        assert failed_job is not None
        assert failed_job.status == "failed"
        assert "secret-token-abc123" not in failed_job.error
        assert "secret-token-abc123" not in str(raised.value)


def test_admin_approval_rejects_a_hash_for_any_other_payload(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        _, _, _, blueprint = make_draft(session)
        change = propose_build_change(session, blueprint.id, "Build", "operator")
        with pytest.raises(ValueError, match="validate_only"):
            record_client_approval(session, change.id, "email-approval", "Client", "admin")
        validate_change_with_platform(session, change.id, "operator")
        poll_change_platform_validation(session, change.id, "operator")
        record_client_approval(session, change.id, "email-approval", "Client", "admin")
        with pytest.raises(ValueError, match="does not match"):
            admin_approve_change(session, change.id, "0" * 64, "admin")
        refreshed = session.get(ChangeRequest, change.id)
        assert refreshed.status == "client_approved"


def test_audit_chain_verifier_detects_payload_tampering_without_disclosing_payload(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Audit verification", "digital_products")
        valid = verify_audit_chain(session)
        assert valid["status"] == "verified"
        assert valid["entry_count"] == 1

        audit = session.scalars(select(AuditLog)).one()
        audit.payload = {"changed": "tampering"}
        tampered = verify_audit_chain(session)
        assert tampered["status"] == "tampered"
        assert tampered["invalid_hash_count"] == 1
        assert audit.id in tampered["first_invalid_entry_ids"]
        assert "payload" not in tampered


def test_audit_chain_verifier_labels_legacy_actor_unbound_hash_as_incomplete(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Legacy audit", "digital_products")
        audit = session.scalars(select(AuditLog)).one()
        audit.entry_hash = payload_sha256(
            {
                "previous_hash": None,
                "action": audit.action,
                "entity_type": audit.entity_type,
                "entity_id": audit.entity_id,
                "client_id": audit.client_id,
                "payload": audit.payload,
            }
        )
        legacy = verify_audit_chain(session)
        assert legacy["status"] == "incomplete"
        assert legacy["legacy_hash_count"] == 1
        assert legacy["invalid_hash_count"] == 0


def test_hint_set_lints_delivery_claims_and_prevents_blueprint_use(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Lint Test", "digital_products", "https://example.com")
        workspace = configure_ads_workspace(session, client.id, actor_id="operator")
        bad_hints = create_hint_set(
            session,
            client.id,
            "unsafe",
            ["Guaranteed delivery to operations leaders evaluating workflow tools."],
            actor_id="operator",
        )
        assert bad_hints.status == "blocked"
        with pytest.raises(ValueError, match="blocked"):
            create_campaign_blueprint(session, client.id, workspace.id, bad_hints.id, "Unsafe", desired_state(), actor_id="operator")


def test_impression_fixed_bid_compiles_per_impression_micros(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client, workspace, hint_set, _ = make_draft(session)
        desired = desired_state()
        desired["campaign"]["billing_event_type"] = "impression"
        desired["ad_group"].pop("max_bid_micros")
        # $60 CPM is $0.06 per impression, or 60,000 currency micros.
        desired["ad_group"]["max_bid_micros"] = 60_000
        blueprint = create_campaign_blueprint(
            session, client.id, workspace.id, hint_set.id, "Impression bid", desired, actor_id="operator"
        )
        compiled = compile_blueprint(session, blueprint.id, "operator")
        assert compiled["valid"]
        ad_group_input = compiled["payload"]["operations"][1]["input"]
        assert ad_group_input["max_bid_micros"] == 60_000
        assert "max_cpm_bid_micros" not in ad_group_input


def test_hint_set_rejects_unreviewed_evidence_as_support(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Evidence gate", "digital_products")
        source = ingest_text(session, client.id, "notes", "Operators compare workflow tools before switching.")
        with pytest.raises(ValueError, match="human-approved"):
            create_hint_set(
                session,
                client.id,
                "switching",
                ["Teams evaluating a process change and comparing workflow products."],
                evidence_ids=[source.evidence[0].id],
                actor_id="operator",
            )


def test_metrics_are_snapshotted_with_explicit_mock_freshness(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client, _, _, _ = make_draft(session)
        snapshot = sync_campaign_insights(
            session,
            client.id,
            "cmpn_mock",
            period="2026-09-01/2026-09-07",
            timezone="America/New_York",
            requested_fields=["impressions", "clicks", "spend"],
            actor_id="operator",
        )
        assert snapshot["provider"] == "mock"
        assert snapshot["freshness_state"] == "mock"
        stored = session.get(AdsInsightSnapshot, snapshot["snapshot_id"])
        assert stored.timezone == "America/New_York"
        assert stored.requested_fields == ["impressions", "clicks", "spend"]
        assert stored.raw_response["data"][0]["spend"] == 0.0
        assert stored.raw_response["request"]["time_ranges[]"] == [
            '{"type": "date_range", "since": "2026-09-01", "until": "2026-09-07", "timezone": "America/New_York"}'
        ]


def test_ad_group_insights_snapshots_preserve_level_for_hint_comparison(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client, _, _, _ = make_draft(session)
        snapshot = sync_entity_insights(
            session,
            client.id,
            "adgrp_mock",
            aggregation_level="ad_group",
            period="2026-09-01/2026-09-07",
            timezone="America/New_York",
            actor_id="operator",
        )
        stored = session.get(AdsInsightSnapshot, snapshot["snapshot_id"])
        assert stored.aggregation_level == "ad_group"
        assert stored.campaign_external_id == "adgrp_mock"  # legacy column stores the external entity ID
        assert stored.metrics["ad_group_id"] == "adgrp_mock"
        assert stored.raw_response["request"]["aggregation_level"] == "ad_group"


def test_real_insights_adapter_routes_to_requested_entity_endpoint(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"object":"list","data":[]}'

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(ads_module, "urlopen", fake_urlopen)
    adapter = GuardedRealAdsAdapter("test-ads-key", "https://ads.example/v1")
    adapter.get_insights("ad_group", "adgrp_123", {"aggregation_level": "ad_group"})
    assert calls[0].full_url == "https://ads.example/v1/ad_groups/adgrp_123/insights?aggregation_level=ad_group"


def test_controlled_comparison_requires_two_documented_arms(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Experiment Test", "digital_products")
        with pytest.raises(ValueError, match="exactly two"):
            create_controlled_experiment(
                session,
                client.id,
                name="Hint framing",
                hypothesis="Specific scenario framing will improve efficiency.",
                changed_variable="context-hint framing",
                primary_metric="cpc",
                guardrails={"max_spend": 100},
                decision_rule="Review after comparable full days.",
                arms=[{"name": "A"}],
                attribution={"window": "30d"},
                timeframe={"start": "2026-10-01", "decision_date": "2026-10-15"},
            )
        experiment = create_controlled_experiment(
            session,
            client.id,
            name="Hint framing",
            hypothesis="Specific scenario framing will improve efficiency.",
            changed_variable="context-hint framing",
            primary_metric="cpc",
            guardrails={"max_spend": 100},
            decision_rule="Review after comparable full days.",
            arms=[
                {"name": "A", "blueprint_id": "one", "aggregation_level": "ad_group", "entity_external_id": "adgrp-one"},
                {"name": "B", "blueprint_id": "two", "aggregation_level": "ad_group", "entity_external_id": "adgrp-two"},
            ],
            attribution={"window": "30d"},
            timeframe={"start": "2026-10-01", "decision_date": "2026-10-15", "timezone": "America/New_York"},
        )
        assert experiment.status == "pre_registered"


def test_mock_snapshots_cannot_support_a_positive_experiment_decision(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Experiment results", "digital_products")
        configure_ads_workspace(session, client.id, ad_account_id="mock_account", actor_id="operator")
        arms = [
            {"name": "A", "aggregation_level": "ad_group", "entity_external_id": "adgrp-a"},
            {"name": "B", "aggregation_level": "ad_group", "entity_external_id": "adgrp-b"},
        ]
        experiment = create_controlled_experiment(
            session,
            client.id,
            name="Mock-only comparison",
            hypothesis="Scenario-specific context may improve clicks.",
            changed_variable="context-hint framing",
            primary_metric="clicks",
            guardrails={"maximum_spend": 100},
            decision_rule="Record as inconclusive unless eligible live snapshots support a review.",
            arms=arms,
            attribution={"source": "OpenAI Ads Insights"},
            timeframe={"start": "2026-09-01", "decision_date": "2026-09-10", "timezone": "America/New_York"},
            actor_id="operator",
        )
        snapshot_ids = {}
        for arm in arms:
            snapshot = sync_entity_insights(
                session,
                client.id,
                arm["entity_external_id"],
                aggregation_level="ad_group",
                period="2026-09-01/2026-09-10",
                timezone="America/New_York",
                actor_id="operator",
            )
            snapshot_ids[arm["name"]] = snapshot["snapshot_id"]

        assessment = evaluate_controlled_experiment(session, experiment.id, snapshot_ids, actor_id="operator")
        assert assessment["status"] == "assessed"
        assert assessment["result"]["data_quality"] == "mock_ineligible"
        assert assessment["result"]["assessment"]["decision_made"] is False
        with pytest.raises(ValueError, match="mock_ineligible"):
            record_controlled_experiment_decision(
                session, experiment.id, "scale", "client-approval-17", "Mock data is not production evidence.", "admin"
            )
        decided = record_controlled_experiment_decision(
            session, experiment.id, "inconclusive", "client-approval-17", "Mock data only.", "admin"
        )
        assert decided.status == "inconclusive"
        assert decided.result["decision"]["causal_claim"] is False


def test_workspace_readiness_never_creates_a_visibility_claim(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Readiness Test", "education")
        result = run_workspace_readiness(session, client.id)
        assert result["ready_for_drafting"] is False
        assert "No visibility simulation" in result["note"]
        assert "Ads workspace is not configured" in result["blockers"]


def test_real_adapter_allows_validate_only_without_mutations(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"id": "bulk_123", "status": "queued"}).encode()

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(ads_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(ads_module, "settings", replace(ads_module.settings, mutations_enabled=False))
    adapter = GuardedRealAdsAdapter("test-ads-key", "https://ads.example/v1")
    result = adapter.validate_bulk_job({"validate_only": True, "partial_failure": False, "operations": []})
    assert result["submitted"] is True
    assert len(calls) == 1
    assert calls[0].full_url.endswith("/bulk_mutation_jobs")
    assert json.loads(calls[0].data.decode())["validate_only"] is True
    assert calls[0].headers.get("Idempotency-key")


def test_secret_intake_is_encrypted_and_never_listed_as_plaintext(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(secret_store, "settings", replace(secret_store.settings, agency_master_key=generate_master_key()))
    with Session() as session:
        client = create_client(session, "Secrets", "digital_products")
        set_client_secret(session, client.id, "OPENAI_ADS_API_KEY", "ads-secret-value", actor_id="admin")
        names = list_client_secret_names(session, client.id)
        assert names[0]["name"] == "OPENAI_ADS_API_KEY"
        assert "ads-secret-value" not in json.dumps(names)
        assert get_client_secret(session, client.id, "OPENAI_ADS_API_KEY") == "ads-secret-value"


def test_secret_delete_removes_local_copy_and_audits_without_upstream_revocation(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(secret_store, "settings", replace(secret_store.settings, agency_master_key=generate_master_key()))
    with Session() as session:
        client = create_client(session, "Secret offboarding", "digital_products")
        set_client_secret(session, client.id, "OPENAI_ADS_API_KEY", "ads-secret-value", actor_id="admin")
        assert delete_client_secret(session, client.id, "OPENAI_ADS_API_KEY", actor_id="admin") is True
        assert list_client_secret_names(session, client.id) == []
        deletion = session.scalars(select(AuditLog).where(AuditLog.action == "client_secret.deleted")).one()
        assert deletion.payload == {"client_id": client.id, "name": "OPENAI_ADS_API_KEY", "backend": "local_encrypted"}
        assert deletion.actor_id == "admin"
        assert deletion.entry_hash
        assert "ads-secret-value" not in json.dumps(deletion.payload)
        assert delete_client_secret(session, client.id, "OPENAI_ADS_API_KEY") is False


def test_azure_secret_delete_uses_soft_delete_without_purging(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    calls = []

    class Poller:
        def result(self):
            calls.append("waited_for_soft_delete")

    class FakeKeyVaultClient:
        def set_secret(self, name, value):
            calls.append(("set", name, value))

        def begin_delete_secret(self, name):
            calls.append(("delete", name))
            return Poller()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(
        secret_store,
        "settings",
        replace(secret_store.settings, secret_backend="azure_key_vault", azure_key_vault_url="https://vault.example/"),
    )
    monkeypatch.setattr(secret_store, "_key_vault_client", FakeKeyVaultClient)
    with Session() as session:
        client = create_client(session, "Azure secret offboarding", "digital_products")
        locator_name = write_key_vault_client_secret(client.id, "OPENAI_ADS_API_KEY", "ads-secret-value")
        register_key_vault_secret_reference(session, client.id, "OPENAI_ADS_API_KEY", locator_name, "admin")
        locator_name = _key_vault_test_name(client.id, "OPENAI_ADS_API_KEY")
        assert delete_client_secret(session, client.id, "OPENAI_ADS_API_KEY", actor_id="admin") is True
        assert ("delete", locator_name) in calls
        assert "waited_for_soft_delete" in calls
        assert list_client_secret_names(session, client.id) == []
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "client_secret.deleted")).one()
        assert audit.payload["backend"] == "azure_key_vault_soft_delete"
        assert audit.entry_hash


def test_key_vault_locator_registration_is_deterministic_and_never_stores_value(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(secret_store, "settings", replace(secret_store.settings, secret_backend="azure_key_vault"))
    with Session() as session:
        client = create_client(session, "Locator validation", "digital_products")
        with pytest.raises(ValueError, match="does not match"):
            register_key_vault_secret_reference(
                session, client.id, "OPENAI_ADS_API_KEY", "arbitrary-vault-name", actor_id="tenant:admin"
            )
        expected_locator = _key_vault_test_name(client.id, "OPENAI_ADS_API_KEY")
        row = register_key_vault_secret_reference(
            session, client.id, "OPENAI_ADS_API_KEY", expected_locator, actor_id="tenant:admin"
        )
        assert row.ciphertext == f"keyvault:{expected_locator}"
        assert "ads-secret" not in row.ciphertext


def test_secret_delete_cli_requires_confirmation(monkeypatch):
    monkeypatch.setattr(cli_module, "settings", replace(cli_module.settings, agency_role="admin"))
    result = CliRunner().invoke(
        cli_module.app,
        ["secret-delete", "client-test-id", "OPENAI_ADS_API_KEY"],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "Cancelled" in result.output


def test_experiment_decision_cli_requires_interactive_admin_confirmation(monkeypatch):
    monkeypatch.setattr(cli_module, "settings", replace(cli_module.settings, agency_role="admin"))
    result = CliRunner().invoke(
        cli_module.app,
        ["experiment-decide", "experiment-id", "scale", "approval-17", "Scale after qualified review."],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "Cancelled" in result.output


def _key_vault_test_name(client_id: str, name: str) -> str:
    import hashlib

    return f"eve-{hashlib.sha256(f'{client_id}:{name}'.encode()).hexdigest()[:40]}"


def test_real_ads_adapter_requires_client_scoped_secret(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(ads_module, "settings", replace(ads_module.settings, ads_mode="real"))
    monkeypatch.setattr(secret_store, "settings", replace(secret_store.settings, agency_master_key=generate_master_key()))
    with Session() as session:
        client = create_client(session, "Scoped Ads", "digital_products")
        with pytest.raises(AdsMutationBlocked, match="No client Ads API key"):
            get_ads_adapter(client.id, session)
        set_client_secret(session, client.id, "OPENAI_ADS_API_KEY", "client-only-secret")
        adapter = get_ads_adapter(client.id, session)
        assert isinstance(adapter, GuardedRealAdsAdapter)
        assert adapter.api_key == "client-only-secret"
        with pytest.raises(AdsMutationBlocked, match="client-scoped"):
            get_ads_adapter()


def test_report_is_persisted_immutable_and_requires_review_before_client_ready(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Report review", "digital_products", "https://example.com")
        source = ingest_text(session, client.id, "approved research", "Buyers compare setup time before switching workflow systems.")
        configure_ads_workspace(session, client.id, ad_account_id="account_test", actor_id="admin")

        blocked = create_ads_report(session, client.id, "operator")
        assert blocked.readiness_status == "internal_review_only"
        assert blocked.review_status == "pending_review"
        blocked_hash = blocked.content_sha256
        with pytest.raises(ValueError, match="readiness blockers"):
            review_ads_report(session, blocked.id, "approve", "reviewer")
        assert blocked.content_sha256 == blocked_hash

        evidence_id = source.evidence[0].id
        review_evidence(session, evidence_id, "approved", "reviewer", client.id)
        ready = create_ads_report(session, client.id, "operator")
        assert ready.readiness_status == "reviewable"
        assert any("click-through" in caveat for caveat in ready.report_json["metric_caveats"])
        assert "Metric caveats" in ready.markdown
        original_hash = ready.content_sha256
        original_markdown = ready.markdown
        ready.markdown = original_markdown + "\nTampered after generation"
        with pytest.raises(ValueError, match="integrity check"):
            review_ads_report(session, ready.id, "approve", "admin")
        ready.markdown = original_markdown
        approved = review_ads_report(session, ready.id, "approve", "admin", "Reviewed source and metric caveats.")
        assert approved.review_status == "client_ready"
        assert approved.content_sha256 == original_hash
        sent = mark_ads_report_sent(session, ready.id, "CRM activity ACT-42", "admin")
        assert sent.sent_reference == "CRM activity ACT-42"
        assert sent.sent_by == "admin"
        with pytest.raises(ValueError, match="already been recorded as sent"):
            mark_ads_report_sent(session, ready.id, "CRM activity ACT-43", "admin")


def test_source_redacts_common_direct_identifiers(tmp_path):
    Session = session_factory(tmp_path)
    with Session() as session:
        client = create_client(session, "Privacy", "digital_products")
        source = ingest_text(session, client.id, "note", "Contact jane@example.com or 212-555-0198 about workflow fit.")
        assert "jane@example.com" not in source.content
        assert "212-555-0198" not in source.content
        assert source.redaction_summary == {"email": 1, "phone": 1}


def test_operator_needs_explicit_client_assignment(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)
    monkeypatch.setattr(
        auth_module,
        "settings",
        replace(auth_module.settings, agency_role="operator", agency_operator_id="operator@example.com"),
    )
    with Session() as session:
        client = create_client(session, "Scope Test", "digital_products")
        with pytest.raises(PermissionError, match="not assigned"):
            auth_module.require_client_access(session, client.id)
        grant_client_access(session, client.id, "operator@example.com", "operator", "admin")
        auth_module.require_client_access(session, client.id)


def test_mcp_operator_is_client_scoped_and_cannot_approve(tmp_path, monkeypatch):
    Session = session_factory(tmp_path)

    @contextmanager
    def scoped_session():
        with Session() as session:
            yield session

    monkeypatch.setattr(mcp_server, "session_scope", scoped_session)
    with Session() as session:
        assigned = create_client(session, "Assigned", "digital_products")
        other = create_client(session, "Unassigned", "digital_products")
        grant_client_access(session, assigned.id, "tenant:operator-1", "operator", "admin")

    access_token = AccessToken(token="operator-token", client_id="tenant:operator-1", scopes=["operator"])
    context = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        assert [row["id"] for row in mcp_server.clients_list()["clients"]] == [assigned.id]
        with pytest.raises(PermissionError, match="not assigned"):
            mcp_server.client_profile_update(other.id, {"icp": "unauthorized"})
        with pytest.raises(PermissionError, match="Admin role"):
            mcp_server.change_admin_approve("not-reached", "0" * 64)
    finally:
        auth_context_var.reset(context)


def test_http_bootstrap_auth_rejects_weak_or_insecure_configuration(monkeypatch):
    base = replace(
        auth_module.settings,
        mcp_admin_token="a" * 64,
        mcp_operator_token="b" * 64,
        mcp_public_url="https://eve.example.com/mcp",
    )
    monkeypatch.setattr(auth_module, "settings", base)
    auth_settings, verifier = auth_module.build_http_auth()
    assert str(auth_settings.resource_server_url) == "https://eve.example.com/mcp"
    assert isinstance(verifier, auth_module.StaticTokenVerifier)

    monkeypatch.setattr(auth_module, "settings", replace(base, mcp_admin_token="short"))
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        auth_module.build_http_auth()

    monkeypatch.setattr(auth_module, "settings", replace(base, mcp_operator_token=base.mcp_admin_token))
    with pytest.raises(RuntimeError, match="must be different"):
        auth_module.build_http_auth()

    monkeypatch.setattr(auth_module, "settings", replace(base, mcp_public_url="http://eve.example.com/mcp"))
    with pytest.raises(RuntimeError, match="must use HTTPS"):
        auth_module.build_http_auth()

    monkeypatch.setattr(auth_module, "settings", replace(base, mcp_public_url="http://127.0.0.1:8000/mcp"))
    auth_module.build_http_auth()

    entra = replace(
        base,
        mcp_auth_mode="entra",
        entra_tenant_id="tenant-guid",
        mcp_audience="api://eve",
    )
    monkeypatch.setattr(auth_module, "settings", entra)
    entra_auth, entra_verifier = auth_module.build_http_auth()
    assert str(entra_auth.issuer_url) == "https://login.microsoftonline.com/tenant-guid/v2.0"
    assert isinstance(entra_verifier, auth_module.EntraTokenVerifier)
    monkeypatch.setattr(auth_module, "settings", replace(entra, entra_tenant_id=None))
    with pytest.raises(RuntimeError, match="AZURE_TENANT_ID"):
        auth_module.build_http_auth()

    auth0 = replace(
        base,
        mcp_auth_mode="auth0",
        auth0_domain="eve-test.us.auth0.com",
        auth0_audience="https://eve.example/mcp",
        mcp_public_url="https://eve.example/mcp",
    )
    monkeypatch.setattr(auth_module, "settings", auth0)
    auth0_settings, auth0_verifier = auth_module.build_http_auth()
    assert str(auth0_settings.issuer_url) == "https://eve-test.us.auth0.com/"
    assert str(auth0_settings.resource_server_url) == "https://eve.example/mcp"
    assert isinstance(auth0_verifier, Auth0TokenVerifier)

    monkeypatch.setattr(auth_module, "settings", replace(auth0, auth0_domain=None))
    with pytest.raises(RuntimeError, match="AUTH0_DOMAIN and AUTH0_AUDIENCE"):
        auth_module.build_http_auth()


def test_auth0_http_auth_requires_canonical_audience_and_tools_advertise_scope(monkeypatch):
    auth0 = replace(
        auth_module.settings,
        mcp_auth_mode="auth0",
        mcp_public_url="https://eve.example/mcp",
        auth0_domain="eve-test.us.auth0.com",
        auth0_audience="https://other.example/api",
    )
    monkeypatch.setattr(auth_module, "settings", auth0)
    with pytest.raises(RuntimeError, match="must exactly match AGENCY_MCP_PUBLIC_URL"):
        auth_module.build_http_auth()

    monkeypatch.setattr(mcp_server, "settings", auth0)
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert tools
    assert all(tool.securitySchemes == [{"type": "oauth2", "scopes": ["operator"]}] for tool in tools)


def test_private_executor_worker_is_not_blocked_by_admin_cli_gate(monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "settings",
        replace(cli_module.settings, agency_role="operator", process_role="executor"),
    )
    result = CliRunner().invoke(
        cli_module.app,
        ["executor-worker", "--poll-interval-seconds", "0"],
    )
    assert result.exit_code == 2
    assert "poll_interval_seconds must be between 1 and 300" in result.output


def test_private_executor_worker_stops_cleanly_on_sigterm(tmp_path):
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.db'}",
            "ADS_MODE": "mock",
            "AGENCY_MUTATIONS_ENABLED": "false",
            "AGENCY_ROLE": "operator",
            "EVE_PROCESS_ROLE": "executor",
        }
    )
    bootstrap_env = {**env, "AGENCY_ROLE": "admin"}
    subprocess.run(
        [sys.executable, "-c", "from agency_mcp.cli import app; app()", "db-init"],
        cwd=Path(__file__).resolve().parents[1],
        env=bootstrap_env,
        check=True,
        capture_output=True,
        text=True,
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "from agency_mcp.cli import app; app()", "executor-worker", "--poll-interval-seconds", "5"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout is not None
        startup_lines = []
        while True:
            line = process.stdout.readline()
            startup_lines.append(line)
            if "worker started" in line.lower() or not line:
                break
        startup = "".join(startup_lines)
        assert "worker started" in startup.lower(), startup
        process.terminate()
        tail = process.communicate(timeout=5)[0]
        assert process.returncode == 0
        assert "worker stopped" in tail.lower()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
