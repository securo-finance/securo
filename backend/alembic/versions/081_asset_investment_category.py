"""add investment_category to assets

Revision ID: 081
Revises: 080
Create Date: 2026-08-31

Coarse asset-class bucket (fixed income, equity, pension, ...) derived from
Pluggy's investment type/subtype, for the portfolio chart's "By Type"
grouping. Backfills existing Pluggy-sourced assets from the type/subtype
already sitting in `external_metadata` — this mirrors
`_categorize_investment` in `app/providers/pluggy.py` at the time this
migration was written; keep both in sync if that mapping changes later.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("investment_category", sa.String(length=40), nullable=True),
    )

    op.execute(
        """
        UPDATE assets
        SET investment_category = CASE
            WHEN external_metadata->>'type' = 'FIXED_INCOME' THEN
                CASE WHEN currency <> 'BRL' THEN 'fixed_income_intl' ELSE 'fixed_income' END
            WHEN external_metadata->>'type' = 'EQUITY'
                 AND external_metadata->>'subtype' = 'REAL_ESTATE_FUND' THEN 'real_estate_fund'
            WHEN external_metadata->>'type' IN ('EQUITY', 'ETF') THEN
                CASE WHEN currency <> 'BRL' THEN 'equity_intl' ELSE 'equity' END
            WHEN external_metadata->>'type' = 'MUTUAL_FUND'
                 AND external_metadata->>'subtype' = 'MULTIMARKET_FUND' THEN 'multimarket'
            WHEN external_metadata->>'type' = 'MUTUAL_FUND' THEN
                CASE
                    WHEN currency <> 'BRL' OR external_metadata->>'subtype' = 'OFFSHORE_FUND'
                        THEN 'funds_intl'
                    ELSE 'funds'
                END
            WHEN external_metadata->>'type' = 'COE' THEN 'structured_note'
            WHEN external_metadata->>'type' = 'SECURITY'
                 AND external_metadata->>'subtype' = 'RETIREMENT' THEN 'pension'
            WHEN external_metadata->>'type' = 'OTHER' THEN 'other'
            ELSE NULL
        END
        WHERE source = 'pluggy' AND external_metadata IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("assets", "investment_category")
