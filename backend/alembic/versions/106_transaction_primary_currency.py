"""record the currency used for a transaction's primary amount

Revision ID: 106
Revises: 105
"""

import sqlalchemy as sa

from alembic import op

revision = "106"
down_revision = "105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("amount_primary_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "amount_primary_currency")
