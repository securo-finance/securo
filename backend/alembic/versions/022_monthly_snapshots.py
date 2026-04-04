"""add monthly snapshots

Revision ID: 022
Revises: 021
Create Date: 2026-04-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monthly_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("monthly_period_id", UUID(as_uuid=True), sa.ForeignKey("monthly_periods.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "period", name="uq_monthly_snapshots_user_period"),
        sa.UniqueConstraint("monthly_period_id", name="uq_monthly_snapshots_monthly_period_id"),
    )
    op.create_index("ix_monthly_snapshots_user_id", "monthly_snapshots", ["user_id"])
    op.create_index("ix_monthly_snapshots_period", "monthly_snapshots", ["period"])


def downgrade() -> None:
    op.drop_index("ix_monthly_snapshots_period", table_name="monthly_snapshots")
    op.drop_index("ix_monthly_snapshots_user_id", table_name="monthly_snapshots")
    op.drop_table("monthly_snapshots")
