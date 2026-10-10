"""add transaction_category_allocations table

Introduces the category-split feature: a single transaction (e.g. a
supermarket bill) can be split across multiple categories so budgets and
reports attribute each portion to its own category. Each allocation carries:

- transaction_id  FK → transactions.id  CASCADE  NOT NULL
- workspace_id    FK → workspaces.id    CASCADE  NOT NULL  (indexed)
- category_id     FK → categories.id   NO ACTION NOT NULL  (indexed)
  — mirrors transactions.category_id which is also NO ACTION.
  Deletion of a category referenced by allocations is blocked by the FK;
  category_service.delete_category handles this via get_category_usage().
- amount          Numeric(15,2)  the part attributed to this category,
  in the parent transaction's currency
- position        SmallInt  preserves row order; the rounding residual
  is assigned to the last row by position
- notes           String(500)  optional label per row

Unique constraint: (transaction_id, category_id) — a category appears at
most once per transaction.

Revision ID: 097
Revises: a9338a1741a6
"""
from typing import Union, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "097"
down_revision: Union[str, Sequence[str], None] = "a9338a1741a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction_category_allocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NO ondelete — category deletion is guarded at the service layer.
        sa.Column(
            "category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_transaction_category_allocations_workspace_id",
        "transaction_category_allocations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_transaction_category_allocations_category_id",
        "transaction_category_allocations",
        ["category_id"],
    )
    op.create_unique_constraint(
        "uq_txn_cat_alloc_tx_category",
        "transaction_category_allocations",
        ["transaction_id", "category_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_txn_cat_alloc_tx_category",
        "transaction_category_allocations",
        type_="unique",
    )
    op.drop_index(
        "ix_transaction_category_allocations_category_id",
        table_name="transaction_category_allocations",
    )
    op.drop_index(
        "ix_transaction_category_allocations_workspace_id",
        table_name="transaction_category_allocations",
    )
    op.drop_table("transaction_category_allocations")
