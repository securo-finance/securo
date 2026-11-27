"""widen asset valuations to the money columns' thirteen integer digits

`asset_values.amount` and `gross_amount` were Numeric(15, 6): nine integer
digits, so a valuation of one billion failed at flush with a numeric field
overflow and a 500. One billion is an ordinary flat in IDR, VND, KRW or JPY,
and transaction amounts and purchase prices already hold thirteen digits.
Numeric(19, 6) keeps the six decimals and gives the same thirteen.

Revision ID: 137
Revises: 136
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "137"
down_revision: Union[str, None] = "136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "asset_values",
        "amount",
        type_=sa.Numeric(precision=19, scale=6),
        existing_type=sa.Numeric(precision=15, scale=6),
        existing_nullable=False,
    )
    op.alter_column(
        "asset_values",
        "gross_amount",
        type_=sa.Numeric(precision=19, scale=6),
        existing_type=sa.Numeric(precision=15, scale=6),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Fails while a stored valuation has more than nine integer digits; those
    # rows have to be removed or reduced by hand first.
    op.alter_column(
        "asset_values",
        "gross_amount",
        type_=sa.Numeric(precision=15, scale=6),
        existing_type=sa.Numeric(precision=19, scale=6),
        existing_nullable=True,
    )
    op.alter_column(
        "asset_values",
        "amount",
        type_=sa.Numeric(precision=15, scale=6),
        existing_type=sa.Numeric(precision=19, scale=6),
        existing_nullable=False,
    )
