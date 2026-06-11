"""add per-share price to asset_values for ledger-accurate value charts

Revision ID: 061
Revises: 060
Create Date: 2026-06-11

Market-priced holdings stored their value history as `amount = units × price`
using the *current* quantity, so entering a backdated buy/sell didn't reshape
the historical line (the value chart became inconsistent with the ledger).

We now keep a quantity-independent per-share `price` on each value row and
rebuild the chart as `ledger_quantity(date) × price(date)`. This migration adds
the column and backfills it for existing market-priced value rows as
`amount / units` (units were constant at this point, so this recovers the true
per-share price). Manual/growth assets keep `price` NULL and use `amount`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("asset_values", sa.Column("price", sa.Numeric(precision=18, scale=6), nullable=True))

    # Backfill per-share price for market-priced holdings: price = amount / units.
    op.get_bind().execute(
        sa.text(
            """
            UPDATE asset_values v
            SET price = ROUND(v.amount / a.units, 6)
            FROM assets a
            WHERE v.asset_id = a.id
              AND a.valuation_method = 'market_price'
              AND a.units IS NOT NULL AND a.units > 0
            """
        )
    )


def downgrade() -> None:
    op.drop_column("asset_values", "price")
