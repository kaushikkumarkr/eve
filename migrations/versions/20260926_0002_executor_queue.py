"""add private Ads executor queue

Revision ID: 20260926_0002
Revises: 20260926_0001
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0002"
down_revision = "20260926_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ads_executor_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("change_id", sa.String(length=36), sa.ForeignKey("change_requests.id")),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.String(length=200)),
        sa.Column("claimed_by", sa.String(length=200)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ads_executor_jobs_client_id", "ads_executor_jobs", ["client_id"])
    op.create_index("ix_ads_executor_jobs_change_id", "ads_executor_jobs", ["change_id"])
    op.create_index("ix_ads_executor_jobs_status", "ads_executor_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("ads_executor_jobs")
