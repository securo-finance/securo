"""add credit card fields to accounts

Revision ID: 025
Revises: 024
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("credit_limit", sa.Numeric(precision=15, scale=2), nullable=True)
    )
    op.add_column("accounts", sa.Column("statement_close_day", sa.SmallInteger(), nullable=True))
    op.add_column("accounts", sa.Column("payment_due_day", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "payment_due_day")
    op.drop_column("accounts", "statement_close_day")
    op.drop_column("accounts", "credit_limit")
