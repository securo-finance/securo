"""transactions: review state for rows that arrive from outside

`transactions.is_read` is the "new/read" marker of issue #636: a transaction
that sync or an import creates starts unread so the list can show it as new
until the person opens it or marks everything as read. The column defaults to
true, so every row that already existed counts as seen (nobody wants a whole
history lighting up as new after an upgrade) and every row created in the app
starts read.

Revision ID: 120
Revises: 119
"""

import sqlalchemy as sa

from alembic import op

revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "is_read" in columns:
        return

    op.add_column(
        "transactions",
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "is_read")
