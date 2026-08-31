"""create debt management tables

New, self-contained feature (loans, payroll-deducted loans, overdue credit
cards) tracked separately from the account/transaction ledger. Purely
additive: 4 new tables, no existing table touched. See app/models/debt.py
for the design rationale.

NOTE for future upstream syncs: this migration is fork-only (not part of
securo-finance/securo). If a future `git merge upstream/main` brings in its
own migration numbered 078+, renumber THIS migration (update its own
`revision`/`down_revision`, and the filename) to chain after the new
upstream HEAD instead. Since nothing outside this fork depends on this
migration's revision id, renumbering it is always safe.

Revision ID: 078
Revises: 077
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "debts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("creditor_name", sa.String(length=255), nullable=False),
        sa.Column("contract_reference", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("original_principal", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("current_balance", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="BRL"),
        sa.Column("related_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("opened_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('loan', 'payroll_loan', 'credit_card_overdue', 'other')",
            name="ck_debts_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'negotiating', 'paid_off', 'defaulted')",
            name="ck_debts_status",
        ),
    )
    op.create_index("ix_debts_workspace_id", "debts", ["workspace_id"])

    op.create_table(
        "debt_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("collection_mode", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("interest_rate", sa.Numeric(precision=8, scale=4), nullable=False, server_default="0"),
        sa.Column("installment_amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("num_installments", sa.Integer(), nullable=False),
        sa.Column("first_due_date", sa.Date(), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False, server_default="monthly"),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["debt_id"], ["debts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('original_contract', 'renegotiated', 'simulation')",
            name="ck_debt_plans_kind",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'superseded', 'rejected')",
            name="ck_debt_plans_status",
        ),
        sa.CheckConstraint(
            "collection_mode IN ('payroll_deduction', 'manual')",
            name="ck_debt_plans_collection_mode",
        ),
        sa.CheckConstraint(
            "frequency IN ('weekly', 'monthly', 'quarterly', 'yearly')",
            name="ck_debt_plans_frequency",
        ),
    )
    op.create_index("ix_debt_plans_debt_id", "debt_plans", ["debt_id"])
    op.create_index("ix_debt_plans_workspace_id", "debt_plans", ["workspace_id"])

    op.create_table(
        "debt_installments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("principal_portion", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("interest_portion", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("paid_amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["debt_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'late', 'skipped')",
            name="ck_debt_installments_status",
        ),
    )
    op.create_index("ix_debt_installments_plan_id", "debt_installments", ["plan_id"])
    op.create_index("ix_debt_installments_workspace_id", "debt_installments", ["workspace_id"])
    op.create_index("ix_debt_installments_due_date", "debt_installments", ["due_date"])

    op.create_table(
        "debt_strategy_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False, server_default="avalanche"),
        sa.Column("extra_monthly_amount", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_debt_strategy_settings_workspace_id"),
        sa.CheckConstraint(
            "method IN ('snowball', 'avalanche')",
            name="ck_debt_strategy_settings_method",
        ),
    )


def downgrade() -> None:
    op.drop_table("debt_strategy_settings")
    op.drop_index("ix_debt_installments_due_date", table_name="debt_installments")
    op.drop_index("ix_debt_installments_workspace_id", table_name="debt_installments")
    op.drop_index("ix_debt_installments_plan_id", table_name="debt_installments")
    op.drop_table("debt_installments")
    op.drop_index("ix_debt_plans_workspace_id", table_name="debt_plans")
    op.drop_index("ix_debt_plans_debt_id", table_name="debt_plans")
    op.drop_table("debt_plans")
    op.drop_index("ix_debts_workspace_id", table_name="debts")
    op.drop_table("debts")
