"""add category flow types

Revision ID: 064
Revises: 063
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INCOME_GROUP_NAMES = (
    "income",
    "renda",
    "ingresos",
    "entrate",
    "przychody",
    "доходы",
    "доходи",
)

INCOME_CATEGORY_NAMES = (
    "salary & income",
    "salário & renda",
)


def upgrade() -> None:
    op.add_column(
        "category_groups",
        sa.Column("flow_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("flow_type", sa.String(20), nullable=True),
    )

    income_group_names_sql = ", ".join(f"'{name}'" for name in INCOME_GROUP_NAMES)
    income_category_names_sql = ", ".join(f"'{name}'" for name in INCOME_CATEGORY_NAMES)

    op.execute(
        f"""
        UPDATE category_groups
        SET flow_type = CASE
            WHEN lower(name) IN ({income_group_names_sql}) THEN 'income'
            ELSE 'expense'
        END
        WHERE flow_type IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE categories
        SET flow_type = CASE
            WHEN group_id IN (
                SELECT id FROM category_groups WHERE flow_type = 'income'
            ) THEN 'income'
            WHEN lower(name) IN ({income_category_names_sql}) THEN 'income'
            ELSE 'expense'
        END
        WHERE flow_type IS NULL
        """
    )

    op.alter_column(
        "category_groups",
        "flow_type",
        nullable=False,
        server_default="expense",
    )
    op.alter_column(
        "categories",
        "flow_type",
        nullable=False,
        server_default="expense",
    )
    op.create_index(
        "ix_category_groups_workspace_flow_type",
        "category_groups",
        ["workspace_id", "flow_type"],
    )
    op.create_index(
        "ix_categories_workspace_flow_type",
        "categories",
        ["workspace_id", "flow_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_categories_workspace_flow_type", table_name="categories")
    op.drop_index("ix_category_groups_workspace_flow_type", table_name="category_groups")
    op.drop_column("categories", "flow_type")
    op.drop_column("category_groups", "flow_type")
