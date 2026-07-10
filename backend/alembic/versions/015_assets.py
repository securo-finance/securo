"""add assets and asset_values tables

Revision ID: 015
Revises: 014
Create Date: 2026-03-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("units", sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column("valuation_method", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("sell_date", sa.Date(), nullable=True),
        sa.Column("sell_price", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("growth_type", sa.String(20), nullable=True),
        sa.Column("growth_rate", sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column("growth_frequency", sa.String(20), nullable=True),
        sa.Column("growth_start_date", sa.Date(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_assets_user_id", "assets", ["user_id"])

    op.create_table(
        "asset_values",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.UUID(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=15, scale=6), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
    )
    op.create_index("ix_asset_values_asset_date", "asset_values", ["asset_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_asset_values_asset_date", "asset_values")
    op.drop_table("asset_values")
    op.drop_index("ix_assets_user_id", "assets")
    op.drop_table("assets")
