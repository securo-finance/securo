"""flag transfers whose destination amount was entered by the user (issue #529)

Revision ID: 067
Revises: 066
Create Date: 2026-08-12

A cross-currency transfer can be created either by converting the source amount
at the market rate or by typing the amount that actually landed on the
destination account. In the second case both amounts are observed facts, so
editing one leg must not silently re-derive the other from an FX rate.

Additive, NOT NULL with a server default of false: existing rows keep the old
behaviour (amounts were derived, so they may be re-derived), and the column is
only ever true for pairs created with an explicit destination amount.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "transfer_amount_explicit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "transfer_amount_explicit")
