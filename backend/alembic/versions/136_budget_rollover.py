"""budgets.rollover and reset_rollover — opt-in cumulative budget carryover (#192)

Adds opt-in ``rollover`` and one-month boundary ``reset_rollover`` booleans
to ``budgets``, defaulting to ``false`` so existing budgets keep single-month
behavior.

Revision ID: 136
Revises: 135
"""

import sqlalchemy as sa
from alembic import op

revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("rollover", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "budgets",
        sa.Column("reset_rollover", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("budgets", "reset_rollover")
    op.drop_column("budgets", "rollover")
