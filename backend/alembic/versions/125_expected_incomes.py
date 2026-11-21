"""store each month's expected income as its own workspace row

Revision ID: 125
Revises: 124
Create Date: 2026-11-20

The issue #1104 comparison needs the month's income entered by hand, once
per workspace and month, so one row keyed by the two holds the figure and
the currency it was typed in. Nothing is converted here: the display value
is computed on read (ADR 0008), and no `amount_primary` is stored, because
the restamp of #1561 does not cover this table and a stored cache would
stay in the old currency after a display switch.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "125"
down_revision: Union[str, None] = "124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expected_incomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "month", name="uq_expected_income_per_workspace_month"
        ),
    )
    op.create_index("ix_expected_incomes_workspace_id", "expected_incomes", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_expected_incomes_workspace_id", table_name="expected_incomes")
    op.drop_table("expected_incomes")
