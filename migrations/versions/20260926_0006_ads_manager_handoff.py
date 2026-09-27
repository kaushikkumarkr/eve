"""official Ads Manager handoff and operator-reported imports

Revision ID: 20260926_0006
Revises: 20260926_0005
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0006"
down_revision = "20260926_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ads_manager_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(300), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="operator_attested"),
        sa.Column("connected_by", sa.String(200), nullable=False),
        sa.Column("access_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("client_id", name="uq_ads_manager_connection_client"),
    )
    op.create_index("ix_ads_manager_connections_client_id", "ads_manager_connections", ["client_id"])
    op.create_table(
        "ads_external_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(300), nullable=False),
        sa.Column("action_type", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_external_id", sa.String(300)),
        sa.Column("external_reference", sa.String(300), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_reference", sa.String(2048)),
        sa.Column("approved_payload_sha256", sa.String(64)),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", "external_reference", name="uq_ads_external_action_reference"),
    )
    op.create_index("ix_ads_external_actions_client_id", "ads_external_actions", ["client_id"])
    op.create_table(
        "ads_import_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(300), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(300), nullable=False),
        sa.Column("source_reference", sa.String(2048)),
        sa.Column("provider", sa.String(50), nullable=False, server_default="chatgpt_ads_manager_operator_import"),
        sa.Column("imported_by", sa.String(200), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("client_id", "content_sha256", name="uq_ads_import_client_hash"),
    )
    op.create_index("ix_ads_import_records_client_id", "ads_import_records", ["client_id"])
    op.create_index("ix_ads_import_records_content_sha256", "ads_import_records", ["content_sha256"])


def downgrade() -> None:
    op.drop_index("ix_ads_import_records_content_sha256", table_name="ads_import_records")
    op.drop_index("ix_ads_import_records_client_id", table_name="ads_import_records")
    op.drop_table("ads_import_records")
    op.drop_index("ix_ads_external_actions_client_id", table_name="ads_external_actions")
    op.drop_table("ads_external_actions")
    op.drop_index("ix_ads_manager_connections_client_id", table_name="ads_manager_connections")
    op.drop_table("ads_manager_connections")
