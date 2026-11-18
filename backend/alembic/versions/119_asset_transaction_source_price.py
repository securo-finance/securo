"""asset transaction source price: what an imported order paid, before conversion

An order imported in another currency than its holding's is converted at its
date's rate. ``source_currency`` and ``source_price`` keep the file's own
values, so a second import of the same file recognises the order even when the
exchange table has changed since (#1358). Both are NULL on every other row.

Revision ID: 119
Revises: 118
"""

import sqlalchemy as sa

from alembic import op

revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset_transactions", sa.Column("source_currency", sa.String(3), nullable=True))
    op.add_column(
        "asset_transactions",
        sa.Column("source_price", sa.Numeric(precision=18, scale=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("asset_transactions", "source_price")
    op.drop_column("asset_transactions", "source_currency")
