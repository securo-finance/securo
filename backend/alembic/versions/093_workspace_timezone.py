"""letting a workspace keep its own calendar

The application timezone decides which day "today" is for balances,
budgets, due dates and recurring transactions. One installation can hold
workspaces whose books are kept in different places, and a person who
closes the month in Sao Paulo should not see it turn over at Tokyo's
midnight because another workspace on the same server lives there.

So a workspace can name its own timezone. The column is nullable and
null means "follow the application timezone", which is exactly what
every existing workspace did before this migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "093"
down_revision = "092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("timezone", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "timezone")
