"""add market price fields to assets (ticker-based valuation via yfinance)

Revision ID: 037
Revises: 036
Create Date: 2026-04-21

Adds ticker + cached-quote columns so an asset can be valued from a live
market price instead of manual entry or a growth rule. Used by the new
``market_price`` valuation method: the user picks a ticker (AAPL, BTC-USD,
PETR4.SA...), enters quantity, and a scheduled task keeps the price fresh.

Nothing is backfilled — existing assets stay on their current method.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("ticker", sa.String(32), nullable=True))
    op.add_column("assets", sa.Column("ticker_exchange", sa.String(32), nullable=True))
    op.add_column(
        "assets",
        sa.Column("last_price", sa.Numeric(precision=18, scale=6), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("last_price_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Find all market-priced assets efficiently during the refresh task.
    op.create_index(
        "ix_assets_market_price",
        "assets",
        ["valuation_method"],
        postgresql_where=sa.text("valuation_method = 'market_price'"),
    )


def downgrade() -> None:
    op.drop_index("ix_assets_market_price", "assets")
    op.drop_column("assets", "last_price_at")
    op.drop_column("assets", "last_price")
    op.drop_column("assets", "ticker_exchange")
    op.drop_column("assets", "ticker")
