"""link reimbursements and refunds to the transaction they pay back, so user
P&L counts only the net (issue #1126, ADR 0033)

Revision ID: 136
Revises: 135
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reimbursement_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "anchor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "counterpart_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("anchor_id", "counterpart_id", name="uq_reimbursement_allocation_pair"),
        sa.CheckConstraint("amount > 0", name="ck_reimbursement_allocation_amount_positive"),
        sa.CheckConstraint(
            "anchor_id <> counterpart_id", name="ck_reimbursement_allocation_distinct"
        ),
    )
    for index_name, column in (
        ("ix_reimbursement_allocations_workspace_id", "workspace_id"),
        ("ix_reimbursement_allocations_anchor_id", "anchor_id"),
        ("ix_reimbursement_allocations_counterpart_id", "counterpart_id"),
    ):
        op.create_index(index_name, "reimbursement_allocations", [column])


def downgrade() -> None:
    op.drop_table("reimbursement_allocations")
