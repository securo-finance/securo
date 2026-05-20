"""Add is_ignored flag to categories and transactions

Revision ID: 050
Revises: 049
Create Date: 2026-05-19

When is_ignored is True:
- For transactions: excluded from reports, dashboard aggregations, and budget calculations
- For categories: transactions in these categories are excluded from the above
"""

from alembic import op
import sqlalchemy as sa

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_ignored column to categories table
    op.add_column(
        "categories",
        sa.Column(
            "is_ignored",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Add is_ignored column to transactions table
    op.add_column(
        "transactions",
        sa.Column(
            "is_ignored",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Create index on transactions.is_ignored for query filtering efficiency
    op.create_index(
        "ix_transactions_is_ignored",
        "transactions",
        ["is_ignored"],
    )


def downgrade() -> None:
    # Drop index
    op.drop_index("ix_transactions_is_ignored", table_name="transactions")

    # Remove columns
    op.drop_column("transactions", "is_ignored")
    op.drop_column("categories", "is_ignored")
