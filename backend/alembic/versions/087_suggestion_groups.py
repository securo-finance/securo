"""a suggestion that names several invoices at once

One payment can settle several invoices: a client clearing three of
their own in a transfer, a commercial arrangement paying a month at once.
When the engine is sure, it writes them all. When it is not, the question
it asks has to be the whole question: *does this payment cover these
three?* Not three separate questions a person could answer inconsistently
and end up with a payment spread across two debts and short on a third.

So a suggestion may belong to a group, and a group is accepted or
declined as one thing. Null for the ordinary single-invoice case, which
is most of them.

Revision ID: 086
Revises: 085
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_suggestions",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # The queue reads a group whole, every time it reads one at all.
    op.create_index(
        "ix_reconciliation_suggestions_group",
        "reconciliation_suggestions",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_suggestions_group", table_name="reconciliation_suggestions"
    )
    op.drop_column("reconciliation_suggestions", "group_id")
