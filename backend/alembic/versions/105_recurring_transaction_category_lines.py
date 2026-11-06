"""split a recurring transaction across several categories

Revision ID: 105
Revises: 104
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "105"
down_revision = "104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    columns = {column["name"] for column in inspector.get_columns("recurring_transactions")}
    if "has_category_lines" not in columns:
        op.add_column(
            "recurring_transactions",
            sa.Column(
                "has_category_lines", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )

    if "recurring_transaction_category_lines" not in inspector.get_table_names():
        op.create_table(
            "recurring_transaction_category_lines",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "recurring_transaction_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("recurring_transactions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # NO ACTION (the default), not RESTRICT: see the model.
            sa.Column(
                "category_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("categories.id"),
                nullable=False,
            ),
            sa.Column("amount", sa.Numeric(15, 2), nullable=False),
            sa.Column("share_type", sa.String(10), nullable=False, server_default="exact"),
            sa.Column("share_pct", sa.Numeric(5, 2), nullable=True),
            sa.Column("position", sa.SmallInteger(), nullable=False),
            sa.Column("notes", sa.String(500), nullable=True),
            # Not null, as in the model, which always sets it.
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "recurring_transaction_id", "position",
                name="uq_recurring_category_line_position",
            ),
            sa.CheckConstraint("amount > 0", name="ck_recurring_category_line_amount_positive"),
            sa.CheckConstraint(
                "share_type IN ('exact', 'percent')",
                name="ck_recurring_category_line_share_type",
            ),
            sa.CheckConstraint(
                "(share_type = 'percent') = (share_pct IS NOT NULL)",
                name="ck_recurring_category_line_share_pct",
            ),
        )

    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("recurring_transaction_category_lines")
    }
    # Short names: PostgreSQL cuts identifiers at 63 characters.
    for index_name, column in (
        ("ix_recurring_category_lines_workspace_id", "workspace_id"),
        ("ix_recurring_category_lines_recurring_id", "recurring_transaction_id"),
        ("ix_recurring_category_lines_category_id", "category_id"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "recurring_transaction_category_lines", [column])


def downgrade() -> None:
    # Give every split template back the category of its largest part, so a
    # rollback never leaves a recurring bill the user categorized without one.
    op.execute("""
        UPDATE recurring_transactions AS r
        SET category_id = largest.category_id
        FROM (
            SELECT DISTINCT ON (recurring_transaction_id) recurring_transaction_id, category_id
            FROM recurring_transaction_category_lines
            ORDER BY recurring_transaction_id, amount DESC, position ASC
        ) AS largest
        WHERE r.id = largest.recurring_transaction_id
    """)
    op.drop_table("recurring_transaction_category_lines")
    op.drop_column("recurring_transactions", "has_category_lines")
