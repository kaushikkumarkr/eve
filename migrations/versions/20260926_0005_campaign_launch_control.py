"""campaign activation approvals and safety envelope

Revision ID: 20260926_0005
Revises: 20260926_0004
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0005"
down_revision = "20260926_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ads_executor_jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "campaign_launches",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(length=300), nullable=False),
        sa.Column("campaign_id", sa.String(length=300), nullable=False),
        sa.Column("ad_group_id", sa.String(length=300), nullable=False),
        sa.Column("ad_id", sa.String(length=300), nullable=False),
        sa.Column("max_spend_micros", sa.Integer(), nullable=False),
        sa.Column("spend_period", sa.String(length=20), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="proposed"),
        sa.Column("preflight", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("client_approval_reference", sa.String(length=500)),
        sa.Column("client_approved_by", sa.String(length=200)),
        sa.Column("client_approved_at", sa.DateTime(timezone=True)),
        sa.Column("admin_approved_by", sa.String(length=200)),
        sa.Column("admin_approved_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("execution", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_spend_micros > 0", name="ck_campaign_launch_positive_spend_cap"),
        sa.CheckConstraint("spend_period IN ('daily', 'lifetime')", name="ck_campaign_launch_spend_period"),
    )
    op.create_index("ix_campaign_launches_client_id", "campaign_launches", ["client_id"])
    op.create_index("ix_campaign_launches_status", "campaign_launches", ["status"])
    op.create_index("ix_campaign_launches_payload_sha256", "campaign_launches", ["payload_sha256"])


def downgrade() -> None:
    op.drop_index("ix_campaign_launches_payload_sha256", table_name="campaign_launches")
    op.drop_index("ix_campaign_launches_status", table_name="campaign_launches")
    op.drop_index("ix_campaign_launches_client_id", table_name="campaign_launches")
    op.drop_table("campaign_launches")
    op.drop_column("ads_executor_jobs", "priority")
