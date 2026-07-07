"""link recurring bills to actual transactions (issue #116)

Revision ID: 064
Revises: 063
Create Date: 2026-07-02

Adds the plumbing to reconcile recurring bills with the real transactions that
pay them, so bank-synced charges stop duplicating generated recurring rows.

- transactions.recurring_transaction_id: FK to the recurring bill a transaction
  fulfills. Nullable, ON DELETE SET NULL (removing a bill just unlinks its
  transactions). Indexed for the match lookups.
- recurring_transactions.auto_generate: when True (default, preserving today's
  behavior) generate_pending materializes due occurrences into transactions;
  when False the bill stays a projection until a real charge is matched to it.

Both changes are additive and nullable/defaulted, so existing rows are
unaffected: every current recurring bill keeps auto_generate = True.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column(
            "auto_generate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("recurring_transaction_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_transactions_recurring_transaction_id",
        "transactions",
        ["recurring_transaction_id"],
    )
    op.create_foreign_key(
        "fk_transactions_recurring_transaction_id",
        "transactions",
        "recurring_transactions",
        ["recurring_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_transactions_recurring_transaction_id",
        "transactions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_transactions_recurring_transaction_id",
        table_name="transactions",
    )
    op.drop_column("transactions", "recurring_transaction_id")
    op.drop_column("recurring_transactions", "auto_generate")
