"""installments, and debt settled without money

Two ways the money of one invoice fails to be one amount on one date,
both kept inside the invoice rather than becoming features of their own.

  - `invoice_installments`: the dates the money is expected on ("3x",
    "50% upfront"). They add up to the total, and the invoice's own
    `due_date` becomes the last of them so every existing sort and
    filter keeps its meaning. Nothing is allocated to an installment:
    settled money covers the schedule first-to-last when read.
  - `invoice_deductions`: tax the client withheld, a fee the gateway
    kept. They close the debt without counting as received, so the
    invoice reads as paid while "received this month" stays cash.

Revision ID: 095
Revises: 094
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "095"
down_revision = "094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_installments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "position", name="uq_invoice_installments_position"),
        sa.CheckConstraint("amount > 0", name="ck_invoice_installments_amount_positive"),
    )
    op.create_index("ix_invoice_installments_invoice", "invoice_installments", ["invoice_id"])
    op.create_index(
        "ix_invoice_installments_workspace_id", "invoice_installments", ["workspace_id"]
    )

    op.create_table(
        "invoice_deductions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("tax_kind", sa.String(length=30), nullable=True),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deducted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_invoice_deductions_amount_positive"),
        sa.CheckConstraint(
            "kind IN ('withholding_tax', 'gateway_fee', 'fx_difference', 'other')",
            name="ck_invoice_deductions_kind",
        ),
    )
    op.create_index("ix_invoice_deductions_invoice", "invoice_deductions", ["invoice_id"])
    op.create_index(
        "ix_invoice_deductions_workspace_id", "invoice_deductions", ["workspace_id"]
    )


def downgrade() -> None:
    # Dropping the deductions reopens the balance they closed, which is
    # the honest reading of a schema that no longer knows about them.
    op.drop_index("ix_invoice_deductions_workspace_id", table_name="invoice_deductions")
    op.drop_index("ix_invoice_deductions_invoice", table_name="invoice_deductions")
    op.drop_table("invoice_deductions")
    op.drop_index("ix_invoice_installments_workspace_id", table_name="invoice_installments")
    op.drop_index("ix_invoice_installments_invoice", table_name="invoice_installments")
    op.drop_table("invoice_installments")
