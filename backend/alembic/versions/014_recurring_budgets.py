"""add recurring budgets support

Revision ID: 014
Revises: 013
Create Date: 2026-03-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.drop_constraint("uq_budget_per_category_month", "budgets", type_="unique")
    op.create_unique_constraint(
        "uq_budget_per_category_month_type",
        "budgets",
        ["user_id", "category_id", "month", "is_recurring"],
    )
    op.create_index(
        "ix_budgets_recurring_lookup",
        "budgets",
        ["user_id", "category_id", "month"],
        postgresql_where=sa.text("is_recurring = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_budgets_recurring_lookup", "budgets")
    op.drop_constraint("uq_budget_per_category_month_type", "budgets", type_="unique")
    op.create_unique_constraint(
        "uq_budget_per_category_month",
        "budgets",
        ["user_id", "category_id", "month"],
    )
    op.drop_column("budgets", "is_recurring")
