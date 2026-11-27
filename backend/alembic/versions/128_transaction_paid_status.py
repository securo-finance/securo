"""let the user mark a transaction as paid or not (issue #15)

Revision ID: 128
Revises: 127

Going forward, a row that came from a bank statement is money that already
moved, so it is born paid; a row the user types, a transfer they record or the
placeholder a recurring bill generates for a date ahead is born unpaid.

Every row dated today or earlier is taken as paid. A history written before
this mark existed is money the user recorded as having happened, and opening
the app on a list of years of "unpaid" would be noise, not information. A row
dated after today keeps the new column's default (unpaid): it has not happened
yet, and marking a scheduled installment in 2030 paid would be wrong — the same
reason the placeholder a recurring bill generates ahead starts unpaid. Only the
new column is written; no other value on any row changes, and the column does
not feed a total, a balance or a report.
"""

import sqlalchemy as sa
from alembic import op

revision = "128"
down_revision = "127"
branch_labels = None
depends_on = None

# Everything stored up to today was recorded as done; the mark starts meaning
# something only for rows created from here on, and rows dated ahead are still
# waiting to happen. CURRENT_DATE is the database's day.
BACKFILL_SQL = """
UPDATE transactions
SET is_paid = true
WHERE date <= CURRENT_DATE
"""


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "is_paid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(sa.text(BACKFILL_SQL))


def downgrade() -> None:
    op.drop_column("transactions", "is_paid")
