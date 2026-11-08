"""split a transaction across several categories

Revision ID: 104
Revises: 103
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "104"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "has_category_lines" not in transaction_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "has_category_lines",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if "transaction_category_lines" not in inspector.get_table_names():
        op.create_table(
            "transaction_category_lines",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "transaction_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("transactions.id", ondelete="CASCADE"),
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
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "transaction_id", "position", name="uq_transaction_category_line_position"
            ),
            sa.CheckConstraint("amount > 0", name="ck_transaction_category_line_amount_positive"),
            sa.CheckConstraint(
                "share_type IN ('exact', 'percent')",
                name="ck_transaction_category_line_share_type",
            ),
            sa.CheckConstraint(
                "(share_type = 'percent') = (share_pct IS NOT NULL)",
                name="ck_transaction_category_line_share_pct",
            ),
        )

    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("transaction_category_lines")
    }
    for index_name, column in (
        ("ix_transaction_category_lines_workspace_id", "workspace_id"),
        ("ix_transaction_category_lines_transaction_id", "transaction_id"),
        ("ix_transaction_category_lines_category_id", "category_id"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "transaction_category_lines", [column])


def downgrade() -> None:
    # Give every split transaction back a single category, the one of its
    # largest line (the first one on a tie), so rolling back never leaves a
    # transaction the user had categorized showing as uncategorized.
    op.execute("""
        UPDATE transactions AS t
        SET category_id = largest.category_id
        FROM (
            SELECT DISTINCT ON (transaction_id) transaction_id, category_id
            FROM transaction_category_lines
            ORDER BY transaction_id, amount DESC, position ASC
        ) AS largest
        WHERE t.id = largest.transaction_id
    """)
    op.drop_table("transaction_category_lines")
    op.drop_column("transactions", "has_category_lines")
