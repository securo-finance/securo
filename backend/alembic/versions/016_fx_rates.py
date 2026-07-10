"""add fx_rates table

Revision ID: 016
Revises: 015
Create Date: 2026-03-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_fx_rate_base_quote_date",
        "fx_rates",
        ["base_currency", "quote_currency", "date"],
    )
    op.create_index(
        "ix_fx_rates_quote_date",
        "fx_rates",
        ["quote_currency", "date"],
    )


def downgrade() -> None:
    op.drop_index("ix_fx_rates_quote_date", table_name="fx_rates")
    op.drop_constraint("uq_fx_rate_base_quote_date", "fx_rates", type_="unique")
    op.drop_table("fx_rates")
