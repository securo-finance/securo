"""move current budget state onto categories and flatten legacy budget rows

Revision ID: 020
Revises: 019
Create Date: 2026-04-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("has_budget", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "categories",
        sa.Column("budget_amount", sa.Numeric(precision=15, scale=2), nullable=True),
    )

    conn = op.get_bind()
    categories = sa.table(
        "categories",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("has_budget", sa.Boolean()),
        sa.column("budget_amount", sa.Numeric(precision=15, scale=2)),
    )
    budgets = sa.table(
        "budgets",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("user_id", UUID(as_uuid=True)),
        sa.column("category_id", UUID(as_uuid=True)),
        sa.column("amount", sa.Numeric(precision=15, scale=2)),
        sa.column("month", sa.Date()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("is_recurring", sa.Boolean()),
    )

    rows = conn.execute(
        sa.select(
            budgets.c.id,
            budgets.c.user_id,
            budgets.c.category_id,
            budgets.c.amount,
        ).order_by(
            budgets.c.user_id,
            budgets.c.category_id,
            budgets.c.month.desc(),
            budgets.c.created_at.desc(),
            budgets.c.id.desc(),
        )
    ).fetchall()

    keep_ids = []
    seen_keys = set()
    for row in rows:
        key = (row.user_id, row.category_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        keep_ids.append(row.id)
        conn.execute(
            categories.update()
            .where(categories.c.id == row.category_id)
            .values(has_budget=True, budget_amount=row.amount)
        )

    if keep_ids:
        conn.execute(
            budgets.delete().where(sa.not_(budgets.c.id.in_(keep_ids)))
        )
        conn.execute(
            budgets.update()
            .where(budgets.c.id.in_(keep_ids))
            .values(is_recurring=False)
        )

    op.alter_column("categories", "has_budget", server_default=None)


def downgrade() -> None:
    op.drop_column("categories", "budget_amount")
    op.drop_column("categories", "has_budget")
