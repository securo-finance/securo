"""store asset transaction prices at full precision

Synced fixed-income trades carry unit prices such as 0.01007794 on
quantities in the hundreds of thousands. Six decimals rounds those enough
that quantity * price no longer reproduces the cash the broker reports,
and performance uses that product as the money moved.

Revision ID: 098
Revises: 097
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "098"
down_revision: Union[str, None] = "097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "asset_transactions",
        "price",
        type_=sa.Numeric(precision=28, scale=12),
        existing_type=sa.Numeric(precision=18, scale=6),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "asset_transactions",
        "price",
        type_=sa.Numeric(precision=18, scale=6),
        existing_type=sa.Numeric(precision=28, scale=12),
        existing_nullable=False,
    )
