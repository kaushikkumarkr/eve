"""schedule executor polling without busy waiting

Revision ID: 20260926_0003
Revises: 20260926_0002
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0003"
down_revision = "20260926_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_executor_jobs", sa.Column("not_before_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("ads_executor_jobs", "not_before_at")
