"""Arbitrate reviewer deletion and attribution with an immediate RESTRICT FK.

Revision ID: 104
Revises: 103
"""
from alembic import op

revision = "104"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_transactions_reviewed_by_id", "transactions", type_="foreignkey")
    op.create_foreign_key(
        "fk_transactions_reviewed_by_id", "transactions", "users",
        ["reviewed_by_id"], ["id"], ondelete="RESTRICT", deferrable=False,
    )


def downgrade() -> None:
    op.drop_constraint("fk_transactions_reviewed_by_id", "transactions", type_="foreignkey")
    op.create_foreign_key(
        "fk_transactions_reviewed_by_id", "transactions", "users",
        ["reviewed_by_id"], ["id"], ondelete="SET NULL",
    )
