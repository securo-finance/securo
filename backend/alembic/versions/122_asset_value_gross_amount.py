"""store gross investment values separately from withdrawable balances

Revision ID: 122
Revises: 121
Create Date: 2026-10-04

Connected investment providers may expose both a gross market value and the
net amount currently available to withdraw. Asset totals keep using the net
``amount``; benchmark performance uses ``gross_amount`` when present.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "122"
down_revision: Union[str, None] = "121"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_values",
        sa.Column("gross_amount", sa.Numeric(precision=15, scale=6), nullable=True),
    )

    # Purchase-cost seeds are pre-tax by definition and give the interpolation
    # an exact starting anchor for existing Pluggy holdings.
    op.execute(
        """
        UPDATE asset_values value
        SET gross_amount = value.amount
        FROM assets asset
        WHERE value.asset_id = asset.id
          AND asset.source = 'pluggy'
          AND asset.purchase_date = value.date
          AND asset.purchase_price = value.amount
        """
    )

    # external_metadata is the latest provider snapshot. Backfill its gross
    # amount only onto that asset's latest value; earlier gaps are estimated by
    # the performance read path between exact cost/current anchors. Sold and
    # redeemed holdings are skipped: sync keeps refreshing their metadata (a
    # redemption reports 0) but stops adding values, so the snapshot is newer
    # than their latest value. The column holds at most 9 integer digits: an
    # amount that doesn't fit, even once rounded to 6 decimals, is left null
    # instead of aborting the upgrade. The CASE makes the cast run only on
    # strings the pattern accepted.
    op.execute(
        """
        WITH latest_values AS (
            SELECT DISTINCT ON (asset_id) id, asset_id
            FROM asset_values
            ORDER BY asset_id, date DESC, id DESC
        )
        UPDATE asset_values value
        SET gross_amount = round((asset.external_metadata ->> 'amount')::numeric, 6)
        FROM assets asset, latest_values latest
        WHERE value.id = latest.id
          AND latest.asset_id = asset.id
          AND asset.source = 'pluggy'
          AND asset.sell_date IS NULL
          AND upper(coalesce(asset.external_metadata ->> 'status', '')) <> 'TOTAL_WITHDRAWAL'
          AND CASE
              WHEN asset.external_metadata ->> 'amount' ~ '^-?[0-9]{1,9}([.][0-9]+)?$'
              THEN abs(round((asset.external_metadata ->> 'amount')::numeric, 6)) < 1000000000
              ELSE false
          END
        """
    )


def downgrade() -> None:
    op.drop_column("asset_values", "gross_amount")
