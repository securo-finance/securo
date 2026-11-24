"""let the user flag a transaction to come back to it, with an optional
reminder date and note (issue #56)

Revision ID: 123
Revises: 122
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "123"
down_revision: Union[str, None] = "122"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "is_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("transactions", sa.Column("flag_due_date", sa.Date(), nullable=True))
    op.add_column("transactions", sa.Column("flag_note", sa.String(500), nullable=True))
    # The flagged views read only flagged rows, a small slice of the table.
    op.create_index(
        "ix_transactions_flagged",
        "transactions",
        ["workspace_id", "flag_due_date"],
        postgresql_where=sa.text("is_flagged"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_flagged", table_name="transactions")
    op.drop_column("transactions", "flag_note")
    op.drop_column("transactions", "flag_due_date")
    op.drop_column("transactions", "is_flagged")
