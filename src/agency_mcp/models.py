from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vertical: Mapped[str] = mapped_column(String(100), default="general")
    website: Mapped[str | None] = mapped_column(String(2048))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    sources: Mapped[list["SourceDocument"]] = relationship(back_populates="client")


class ClientAccessGrant(Base):
    """Per-client operator membership; the service, not the model, enforces it."""

    __tablename__ = "client_access_grants"
    __table_args__ = (UniqueConstraint("client_id", "operator_id", name="uq_client_operator_grant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    operator_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="operator")
    granted_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    redaction_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    retrieved_at: Mapped[str | None] = mapped_column(String(100))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    client: Mapped[Client] = relationship(back_populates="sources")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="source")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("source_documents.id"))
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_locator: Mapped[str | None] = mapped_column(String(300))
    evidence_type: Mapped[str] = mapped_column(String(50), default="source_content")
    review_status: Mapped[str] = mapped_column(String(30), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped[SourceDocument | None] = relationship(back_populates="evidence")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    client_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(200))
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ClientSecret(Base):
    """Encrypted local secret or non-secret Azure Key Vault locator; never plaintext."""

    __tablename__ = "client_secrets"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_client_secret_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AdsWorkspace(Base):
    """The account-scoped operating context for one client's Advertiser API work."""

    __tablename__ = "ads_workspaces"
    __table_args__ = (UniqueConstraint("client_id", name="uq_ads_workspace_client"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    ad_account_id: Mapped[str | None] = mapped_column(String(300), index=True)
    credential_name: Mapped[str] = mapped_column(String(100), default="OPENAI_ADS_API_KEY")
    status: Mapped[str] = mapped_column(String(30), default="unconfigured")
    observed_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AdsManagerConnection(Base):
    """Operator-attested metadata for the separate official ChatGPT Ads Manager app."""

    __tablename__ = "ads_manager_connections"
    __table_args__ = (UniqueConstraint("client_id", name="uq_ads_manager_connection_client"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    ad_account_id: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="operator_attested")
    connected_by: Mapped[str] = mapped_column(String(200), nullable=False)
    access_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    notes: Mapped[str] = mapped_column(Text, default="")


class AdsExternalAction(Base):
    """Audit record for a human-performed action in the official Ads Manager app."""

    __tablename__ = "ads_external_actions"
    __table_args__ = (UniqueConstraint("client_id", "external_reference", name="uq_ads_external_action_reference"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    ad_account_id: Mapped[str] = mapped_column(String(300), nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_external_id: Mapped[str | None] = mapped_column(String(300))
    external_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_reference: Mapped[str | None] = mapped_column(String(2048))
    approved_payload_sha256: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdsImportRecord(Base):
    """Immutable provenance/deduplication record for manually imported Ads data."""

    __tablename__ = "ads_import_records"
    __table_args__ = (UniqueConstraint("client_id", "content_sha256", name="uq_ads_import_client_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    ad_account_id: Mapped[str] = mapped_column(String(300), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(2048))
    provider: Mapped[str] = mapped_column(String(50), default="chatgpt_ads_manager_operator_import")
    imported_by: Mapped[str] = mapped_column(String(200), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    snapshot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class HintSet(Base):
    """A versioned, evidence-linked context-hint set owned by a planned ad group."""

    __tablename__ = "hint_sets"
    __table_args__ = (UniqueConstraint("client_id", "name", "version", name="uq_hint_set_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    lint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CampaignBlueprint(Base):
    """A desired state which deterministic code compiles to an Ads bulk payload."""

    __tablename__ = "campaign_blueprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("ads_workspaces.id"), index=True, nullable=False)
    hint_set_id: Mapped[str] = mapped_column(ForeignKey("hint_sets.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    desired_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    compiled_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compiled_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ChangeRequest(Base):
    """Immutable external action with client sign-off and hash-bound admin approval."""

    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    blueprint_id: Mapped[str | None] = mapped_column(ForeignKey("campaign_blueprints.id"), index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="proposed")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pre_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    client_approval_reference: Mapped[str | None] = mapped_column(String(500))
    client_approved_by: Mapped[str | None] = mapped_column(String(200))
    client_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_approved_by: Mapped[str | None] = mapped_column(String(200))
    admin_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CampaignLaunch(Base):
    """Hash-bound client/admin approvals for activating one paused Ads hierarchy."""

    __tablename__ = "campaign_launches"
    __table_args__ = (
        CheckConstraint("max_spend_micros > 0", name="ck_campaign_launch_positive_spend_cap"),
        CheckConstraint("spend_period IN ('daily', 'lifetime')", name="ck_campaign_launch_spend_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    ad_account_id: Mapped[str] = mapped_column(String(300), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(300), nullable=False)
    ad_group_id: Mapped[str] = mapped_column(String(300), nullable=False)
    ad_id: Mapped[str] = mapped_column(String(300), nullable=False)
    max_spend_micros: Mapped[int] = mapped_column(Integer, nullable=False)
    spend_period: Mapped[str] = mapped_column(String(20), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="proposed", index=True)
    preflight: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    client_approval_reference: Mapped[str | None] = mapped_column(String(500))
    client_approved_by: Mapped[str | None] = mapped_column(String(200))
    client_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_approved_by: Mapped[str | None] = mapped_column(String(200))
    admin_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AdsExecutorJob(Base):
    """Key-requiring work queued by control plane and executed by a private worker."""

    __tablename__ = "ads_executor_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    change_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    claimed_by: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdsInsightSnapshot(Base):
    __tablename__ = "ads_insight_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    campaign_external_id: Mapped[str | None] = mapped_column(String(300), index=True)
    period: Mapped[str] = mapped_column(String(100), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(50), default="mock")
    aggregation_level: Mapped[str] = mapped_column(String(30), default="campaign")
    timezone: Mapped[str | None] = mapped_column(String(100))
    requested_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(50), default="mock")
    api_version: Mapped[str | None] = mapped_column(String(30))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdsReport(Base):
    """Immutable report snapshot with explicit human review and share state."""

    __tablename__ = "ads_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    readiness_status: Mapped[str] = mapped_column(String(40), nullable=False)
    review_status: Mapped[str] = mapped_column(String(40), default="pending_review", nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_reference: Mapped[str | None] = mapped_column(String(500))
    sent_by: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyCheck(Base):
    __tablename__ = "policy_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), default="manual-review-required")
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ControlledExperiment(Base):
    """A pre-registered, controlled comparison; never a claimed native split test."""

    __tablename__ = "controlled_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    changed_variable: Mapped[str] = mapped_column(String(300), nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_rule: Mapped[str] = mapped_column(Text, nullable=False)
    arms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timeframe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
