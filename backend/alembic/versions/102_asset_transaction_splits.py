"""let the asset ledger record stock splits

Revision ID: 102
Revises: 101
Create Date: 2026-10-29

The ledger only knew buys and sells. After a stock split the position kept
its old units while the quote moved to the new price, so the holding was
valued at a fraction (or a multiple) of what it really was (issue #944).

A split becomes a third `kind`, with its ratio held as two whole numbers,
`old_units` and `new_units`, so a 3-for-2 or a 1-for-10 reverse split stays
exact. The check constraint keeps that pair on splits and nowhere else, and
above zero. Existing rows are all buys and sells, so they keep NULL in both
columns and nothing is rewritten: the change is purely additive.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "102"
down_revision: Union[str, None] = "101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "asset_transactions"
_CHECK = "ck_asset_transactions_split_units"
_CONDITION = (
    "(kind = 'split' AND old_units IS NOT NULL AND new_units IS NOT NULL"
    " AND old_units > 0 AND new_units > 0)"
    " OR (kind <> 'split' AND old_units IS NULL AND new_units IS NULL)"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("old_units", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("new_units", sa.Integer(), nullable=True))
    op.create_check_constraint(_CHECK, _TABLE, _CONDITION)


def downgrade() -> None:
    # A split can't be stored without its ratio, and a ledger replayed without
    # it leaves the sells after it short of units. Unlike a log or a suggestion
    # it's a record, so it isn't deleted here: rolling back has to remove it on
    # purpose.
    split = op.get_bind().execute(
        sa.text(f"SELECT 1 FROM {_TABLE} WHERE kind = 'split' LIMIT 1")
    ).first()
    if split is not None:
        raise RuntimeError(
            "Cannot remove the split columns because split entries exist in the "
            "asset ledger. Delete them in the app, along with any later sells that "
            "depend on them, and rerun the migration."
        )
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "new_units")
    op.drop_column(_TABLE, "old_units")
