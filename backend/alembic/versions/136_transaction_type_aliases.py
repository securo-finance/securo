"""store expense and income as debit and credit

The API stored ``type`` as given, so a transaction or recurring bill created
with ``expense`` or ``income`` (the agent tools among the callers) kept that
word. Every balance, total and projection reads only ``debit`` and
``credit``, so those rows were left out of all of them. The schemas now
translate the two words; this repairs the rows already stored.

The repaired rows start counting on upgrade, so an account that holds one
shows a different balance afterwards: the one its rows always added up to.

Revision ID: 136
Revises: 135
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "136"
down_revision: Union[str, None] = "135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("transactions", "recurring_transactions")
ALIASES = {"expense": "debit", "income": "credit"}


def upgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        for alias, type_ in ALIASES.items():
            # Any case and padding, as the schemas read it.
            conn.execute(
                sa.text(f"UPDATE {table} SET type = :type WHERE lower(trim(type)) = :alias"),
                {"type": type_, "alias": alias},
            )


def downgrade() -> None:
    # Which rows were stored with the alias is not kept, and debit/credit is
    # what every version reads, so there is nothing to undo.
    pass
