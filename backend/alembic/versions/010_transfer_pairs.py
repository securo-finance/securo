"""add transfer_pair_id to transactions for linked transfer detection

Revision ID: 010
Revises: 009
Create Date: 2026-03-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("transfer_pair_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Partial index for fast partner lookups (only rows that are paired)
    op.create_index(
        "ix_transactions_transfer_pair_id",
        "transactions",
        ["transfer_pair_id"],
        postgresql_where=sa.text("transfer_pair_id IS NOT NULL"),
    )

    # Composite index for the matching algorithm
    op.create_index(
        "ix_transactions_transfer_match",
        "transactions",
        ["user_id", "amount", "date"],
        postgresql_where=sa.text("transfer_pair_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_transfer_match", "transactions")
    op.drop_index("ix_transactions_transfer_pair_id", "transactions")
    op.drop_column("transactions", "transfer_pair_id")
