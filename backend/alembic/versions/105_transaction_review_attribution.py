"""Add explicit nullable review attribution and its complete shape CHECK.

Legacy attribution remains NULL without defaults, backfill, or repair.
Rollback retains columns/data and requires a V2-aware writer build;
destructive downgrade is deliberately unsupported.

Revision ID: 105
Revises: 104
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "105"
down_revision = "104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("review_attribution_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("review_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_transactions_review_attribution_shape",
        "transactions",
        "(("
        "(review_attribution_kind IS NULL AND review_rule_id IS NULL)"
        " OR (review_attribution_kind = 'human'"
        " AND review_rule_id IS NULL AND review_status = 'reviewed'"
        " AND reviewed_at IS NOT NULL AND reviewed_by_id IS NOT NULL)"
        " OR (review_attribution_kind = 'rule'"
        " AND review_rule_id IS NOT NULL AND review_status = 'reviewed'"
        " AND reviewed_at IS NOT NULL AND reviewed_by_id IS NULL)"
        ") IS TRUE)",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 100 does not support destructive downgrade: retain review "
        "attribution columns/data and roll back only to a V2-aware writer build."
    )
