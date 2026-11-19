"""keep the converted amount a user typed for a foreign transaction

Revision ID: 125
Revises: 124

`amount_primary` is a cache in the owner's display currency, rewritten when
that currency changes. A converted amount the user typed (a USD row entered
as costing R$ 1,560) lived only there, so a round trip through another
display currency replaced it with a market conversion (ADR 0028).

The new columns keep the typed amount and the currency it was typed in. Old
rows are left empty: nothing records which stored values were typed and which
were derived from a rate.
"""

import sqlalchemy as sa
from alembic import op

revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("amount_primary_typed", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("amount_primary_typed_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "amount_primary_typed_currency")
    op.drop_column("transactions", "amount_primary_typed")
