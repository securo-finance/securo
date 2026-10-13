"""drop server_default from categories.is_income

Migration a9338a1741a6 added the column with server_default 'false'. That
makes every category an explicit expense, including rows created before the
column existed, which defeats the NULL fallback the reports rely on:

    group = "income" if (is_income or (is_income is None and type == "credit"))

NULL means "no explicit classification — use the txn_type heuristic", and is
the model-level default (Category.is_income is Optional[bool], default None).
Dropping the server_default lets inserts that omit the column land as NULL.

Existing rows are left alone: a stored False may be a deliberate choice made
through the UI, and this migration cannot tell those apart from backfilled
ones.

Revision ID: 099
Revises: 098
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "099"
down_revision: Union[str, Sequence[str], None] = "098"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("categories", "is_income", server_default=None)


def downgrade() -> None:
    op.alter_column("categories", "is_income", server_default="false")
