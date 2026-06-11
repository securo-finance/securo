"""add asset_transactions ledger + cached average_price/realized_gain

Revision ID: 059
Revises: 058
Create Date: 2026-06-10

Introduces a buy/sell ledger for market-priced holdings so a position can be
consolidated by ticker with a weighted-average cost (preço médio) and a
separate transactions view (issue #235).

Purely additive: a new `asset_transactions` table plus two nullable cache
columns on `assets` (`average_price`, `realized_gain`). No existing column is
dropped or overwritten; `asset_values` history is untouched. The backfill only
reads existing `purchase_price`/`units`/`purchase_date`/`sell_*` to reconstruct
the initial buy (and a sell when the holding was already sold), so existing
displayed numbers stay identical. Synced (valuation_method='manual') holdings
are not touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_transactions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.UUID(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),  # buy, sell
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("fee", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_asset_transactions_asset_id", "asset_transactions", ["asset_id"])
    op.create_index("ix_asset_transactions_workspace_id", "asset_transactions", ["workspace_id"])
    op.create_index("ix_asset_transactions_date", "asset_transactions", ["date"])

    op.add_column("assets", sa.Column("average_price", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("assets", sa.Column("realized_gain", sa.Numeric(precision=18, scale=2), nullable=True))

    bind = op.get_bind()

    # Reconstruct an initial buy from each market-priced holding that has a
    # known cost basis. `purchase_price` is the *total* paid, so per-share
    # price = purchase_price / units. Source 'import' flags the backfill.
    bind.execute(
        sa.text(
            """
            INSERT INTO asset_transactions
                (id, asset_id, workspace_id, kind, quantity, price, fee, date, source, created_at)
            SELECT gen_random_uuid(), a.id, a.workspace_id, 'buy', a.units,
                   ROUND(a.purchase_price / a.units, 6), 0,
                   COALESCE(a.purchase_date, CURRENT_DATE), 'import', now()
            FROM assets a
            WHERE a.valuation_method = 'market_price'
              AND a.units IS NOT NULL AND a.units > 0
              AND a.purchase_price IS NOT NULL
            """
        )
    )

    # Holdings that were already fully sold get a matching sell entry.
    bind.execute(
        sa.text(
            """
            INSERT INTO asset_transactions
                (id, asset_id, workspace_id, kind, quantity, price, fee, date, source, created_at)
            SELECT gen_random_uuid(), a.id, a.workspace_id, 'sell', a.units,
                   ROUND(a.sell_price / a.units, 6), 0,
                   a.sell_date, 'import', now()
            FROM assets a
            WHERE a.valuation_method = 'market_price'
              AND a.units IS NOT NULL AND a.units > 0
              AND a.sell_price IS NOT NULL AND a.sell_date IS NOT NULL
            """
        )
    )

    # Cache the derived average price (preço médio) and realized gain.
    bind.execute(
        sa.text(
            """
            UPDATE assets a
            SET average_price = ROUND(a.purchase_price / a.units, 6)
            WHERE a.valuation_method = 'market_price'
              AND a.units IS NOT NULL AND a.units > 0
              AND a.purchase_price IS NOT NULL
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE assets a
            SET realized_gain = a.sell_price - a.purchase_price
            WHERE a.valuation_method = 'market_price'
              AND a.sell_price IS NOT NULL AND a.purchase_price IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("assets", "realized_gain")
    op.drop_column("assets", "average_price")
    op.drop_index("ix_asset_transactions_date", "asset_transactions")
    op.drop_index("ix_asset_transactions_workspace_id", "asset_transactions")
    op.drop_index("ix_asset_transactions_asset_id", "asset_transactions")
    op.drop_table("asset_transactions")
