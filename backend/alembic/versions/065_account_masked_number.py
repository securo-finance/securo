"""add masked_number to accounts (issue #408)

Revision ID: 065
Revises: 064
Create Date: 2026-07-14

Banks often report every account under the same label (typically the account
holder's name), leaving two accounts at one bank indistinguishable in the UI.
Store the last 4 characters of the bank's own identifier for the account so the
accounts list can tell them apart without the user renaming each one by hand.

Only the last 4 characters are kept, never the full IBAN/account number.

Additive and nullable: existing rows are unaffected and backfill themselves on
the next sync, since `masked_number` is provider-owned and refreshed alongside
`name`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("masked_number", sa.String(4), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "masked_number")
