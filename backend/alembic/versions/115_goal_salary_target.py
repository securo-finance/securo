"""add salary-multiple targets to goals

Revision ID: 115
Revises: 114
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "115"
down_revision = "114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("goals")}
    if "salary_recurring_id" not in columns:
        op.add_column(
            "goals",
            sa.Column(
                "salary_recurring_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "recurring_transactions.id",
                    name="fk_goals_salary_recurring_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )
    if "salary_months" not in columns:
        op.add_column("goals", sa.Column("salary_months", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("goals", "salary_months")
    op.drop_column("goals", "salary_recurring_id")
