"""accounts.exclude_from_reports — keep an account visible but out of rollup totals

A tracking or investment account that mirrors an asset/portfolio (or whose
balance already embeds its holdings — issues #679 / #343 family) double-counts
in net worth and reports. This additive boolean says "this account's numbers
are not real money for reporting purposes": the account stays in lists,
ledgers, its own pages and exports, but leaves every rollup total.

No index: the filter rides existing Account selects over a small per-
workspace row count (`is_closed` sets the precedent).

Revision ID: 103
Revises: 102
"""
from alembic import op
import sqlalchemy as sa

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "exclude_from_reports",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "exclude_from_reports")
