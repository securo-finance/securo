"""add initial_amount to goals

Revision ID: 023
Revises: 022
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column(
            "initial_amount",
            sa.Numeric(precision=15, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )
    # Backfill existing goals: set initial_amount = current_amount so existing
    # goals don't falsely show progress from zero.
    op.execute("UPDATE goals SET initial_amount = current_amount")


def downgrade() -> None:
    op.drop_column("goals", "initial_amount")
