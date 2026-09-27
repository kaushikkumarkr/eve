"""initial Ads-only Eve control plane

Revision ID: 20260926_0001
Revises:
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0001"
down_revision = None
branch_labels = None
depends_on = None


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), primary_key=True, nullable=False)


def upgrade() -> None:
    op.create_table(
        "clients",
        id_column(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("vertical", sa.String(length=100), nullable=False, server_default="general"),
        sa.Column("website", sa.String(length=2048)),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "client_access_grants",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("operator_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="operator"),
        sa.Column("granted_by", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "operator_id", name="uq_client_operator_grant"),
    )
    op.create_index("ix_client_access_grants_client_id", "client_access_grants", ["client_id"])
    op.create_index("ix_client_access_grants_operator_id", "client_access_grants", ["operator_id"])
    op.create_table(
        "source_documents",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("redaction_summary", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.String(length=2048)),
        sa.Column("retrieved_at", sa.String(length=100)),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_documents_client_id", "source_documents", ["client_id"])
    op.create_index("ix_source_documents_content_hash", "source_documents", ["content_hash"])
    op.create_table(
        "evidence",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), sa.ForeignKey("source_documents.id")),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=2048)),
        sa.Column("source_locator", sa.String(length=300)),
        sa.Column("evidence_type", sa.String(length=50), nullable=False, server_default="source_content"),
        sa.Column("review_status", sa.String(length=30), nullable=False, server_default="unreviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_client_id", "evidence", ["client_id"])
    op.create_table(
        "audit_logs",
        id_column(),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=36)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("client_id", sa.String(length=36)),
        sa.Column("actor_id", sa.String(length=200)),
        sa.Column("previous_hash", sa.String(length=64)),
        sa.Column("entry_hash", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_client_id", "audit_logs", ["client_id"])
    op.create_index("ix_audit_logs_entry_hash", "audit_logs", ["entry_hash"])
    op.create_table(
        "workflow_jobs",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_jobs_client_id", "workflow_jobs", ["client_id"])
    op.create_table(
        "client_secrets",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "name", name="uq_client_secret_name"),
    )
    op.create_index("ix_client_secrets_client_id", "client_secrets", ["client_id"])
    op.create_table(
        "ads_workspaces",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(length=300)),
        sa.Column("credential_name", sa.String(length=100), nullable=False, server_default="OPENAI_ADS_API_KEY"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="unconfigured"),
        sa.Column("observed_state", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", name="uq_ads_workspace_client"),
    )
    op.create_index("ix_ads_workspaces_client_id", "ads_workspaces", ["client_id"])
    op.create_index("ix_ads_workspaces_ad_account_id", "ads_workspaces", ["ad_account_id"])
    op.create_table(
        "hint_sets",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("hints", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("lint", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "name", "version", name="uq_hint_set_version"),
    )
    op.create_index("ix_hint_sets_client_id", "hint_sets", ["client_id"])
    op.create_table(
        "campaign_blueprints",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("ads_workspaces.id"), nullable=False),
        sa.Column("hint_set_id", sa.String(length=36), sa.ForeignKey("hint_sets.id"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("desired_state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("compiled_payload", sa.JSON(), nullable=False),
        sa.Column("compiled_sha256", sa.String(length=64)),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaign_blueprints_client_id", "campaign_blueprints", ["client_id"])
    op.create_index("ix_campaign_blueprints_workspace_id", "campaign_blueprints", ["workspace_id"])
    op.create_index("ix_campaign_blueprints_hint_set_id", "campaign_blueprints", ["hint_set_id"])
    op.create_index("ix_campaign_blueprints_compiled_sha256", "campaign_blueprints", ["compiled_sha256"])
    op.create_table(
        "change_requests",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("blueprint_id", sa.String(length=36), sa.ForeignKey("campaign_blueprints.id")),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="proposed"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("pre_state", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("client_approval_reference", sa.String(length=500)),
        sa.Column("client_approved_by", sa.String(length=200)),
        sa.Column("client_approved_at", sa.DateTime(timezone=True)),
        sa.Column("admin_approved_by", sa.String(length=200)),
        sa.Column("admin_approved_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_requests_client_id", "change_requests", ["client_id"])
    op.create_index("ix_change_requests_blueprint_id", "change_requests", ["blueprint_id"])
    op.create_index("ix_change_requests_payload_sha256", "change_requests", ["payload_sha256"])
    op.create_table(
        "ads_insight_snapshots",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("campaign_external_id", sa.String(length=300)),
        sa.Column("period", sa.String(length=100), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="mock"),
        sa.Column("aggregation_level", sa.String(length=30), nullable=False, server_default="campaign"),
        sa.Column("timezone", sa.String(length=100)),
        sa.Column("requested_fields", sa.JSON(), nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=False),
        sa.Column("freshness_state", sa.String(length=50), nullable=False, server_default="mock"),
        sa.Column("api_version", sa.String(length=30)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ads_insight_snapshots_client_id", "ads_insight_snapshots", ["client_id"])
    op.create_index("ix_ads_insight_snapshots_campaign_external_id", "ads_insight_snapshots", ["campaign_external_id"])
    op.create_table(
        "policy_checks",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("policy_version", sa.String(length=50), nullable=False, server_default="manual-review-required"),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policy_checks_client_id", "policy_checks", ["client_id"])
    op.create_index("ix_policy_checks_entity_id", "policy_checks", ["entity_id"])
    op.create_table(
        "controlled_experiments",
        id_column(),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("changed_variable", sa.String(length=300), nullable=False),
        sa.Column("primary_metric", sa.String(length=100), nullable=False),
        sa.Column("guardrails", sa.JSON(), nullable=False),
        sa.Column("decision_rule", sa.Text(), nullable=False),
        sa.Column("arms", sa.JSON(), nullable=False),
        sa.Column("attribution", sa.JSON(), nullable=False),
        sa.Column("timeframe", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_controlled_experiments_client_id", "controlled_experiments", ["client_id"])


def downgrade() -> None:
    for table in [
        "controlled_experiments", "policy_checks", "ads_insight_snapshots", "change_requests",
        "campaign_blueprints", "hint_sets", "ads_workspaces", "client_secrets", "workflow_jobs",
        "audit_logs", "evidence", "source_documents", "client_access_grants", "clients",
    ]:
        op.drop_table(table)
