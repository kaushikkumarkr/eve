"""persist immutable Ads reports and human review state

Revision ID: 20260926_0004
Revises: 20260926_0003
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0004"
down_revision = "20260926_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ads_reports",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("readiness_status", sa.String(length=40), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False, server_default="pending_review"),
        sa.Column("generated_by", sa.String(length=200)),
        sa.Column("reviewed_by", sa.String(length=200)),
        sa.Column("review_notes", sa.Text()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_reference", sa.String(length=500)),
        sa.Column("sent_by", sa.String(length=200)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ads_reports_client_id", "ads_reports", ["client_id"])
    op.create_index("ix_ads_reports_content_sha256", "ads_reports", ["content_sha256"])


def downgrade() -> None:
    op.drop_index("ix_ads_reports_content_sha256", table_name="ads_reports")
    op.drop_index("ix_ads_reports_client_id", table_name="ads_reports")
    op.drop_table("ads_reports")
