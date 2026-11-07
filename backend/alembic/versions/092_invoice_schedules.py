"""recurring invoices: schedules, their terms, and the link from an invoice

Two tables and four columns, all saying the same thing: an invoice may
answer for one period of an agreement.

  - `invoice_schedules` is the agreement: a client, a frequency, an
    anchor date, an end condition, and a status that records decisions
    only (`active`, `paused`, `ended`). Recurring revenue, what a
    client paid under it and whether it is behind are derived from its
    invoices, never stored.
  - `invoice_schedule_terms` is the price over time: one row per change,
    each saying from when it applies. A raise recorded today for January
    changes nothing until January.
  - `invoices.schedule_id` / `sequence` / `period_start` / `period_end`
    point an invoice at its agreement and period. The unique
    `(schedule_id, sequence)` is what makes the generation job
    idempotent: a retry hits the index instead of emitting twice.

Revision ID: 092
Revises: 091
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "092"
down_revision = "091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=20), server_default="local", nullable=False),
        sa.Column("external_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("pause_reason", sa.String(length=20), nullable=True),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column("end_reason", sa.String(length=30), nullable=True),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_type", sa.String(length=20), server_default="never", nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("end_count", sa.Integer(), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("next_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["payee_id"], ["payees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "external_source",
            "external_id",
            name="uq_invoice_schedules_workspace_external",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'ended')", name="ck_invoice_schedules_status"
        ),
        sa.CheckConstraint(
            "origin IN ('local', 'imported')", name="ck_invoice_schedules_origin"
        ),
        sa.CheckConstraint(
            "frequency IN ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'yearly')",
            name="ck_invoice_schedules_frequency",
        ),
        sa.CheckConstraint(
            "end_type IN ('never', 'on_date', 'after_count')",
            name="ck_invoice_schedules_end_type",
        ),
        sa.CheckConstraint(
            "(end_type = 'never')"
            " OR (end_type = 'on_date' AND end_date IS NOT NULL)"
            " OR (end_type = 'after_count' AND end_count IS NOT NULL AND end_count > 0)",
            name="ck_invoice_schedules_end_condition",
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN"
            " ('canceled_by_client', 'canceled_by_us', 'completed', 'unpaid', 'other')",
            name="ck_invoice_schedules_end_reason",
        ),
        sa.CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ('manual', 'failures')",
            name="ck_invoice_schedules_pause_reason",
        ),
        sa.CheckConstraint(
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)"
            " OR (status <> 'ended' AND ended_at IS NULL AND end_reason IS NULL)",
            name="ck_invoice_schedules_ended_fields",
        ),
        sa.CheckConstraint("next_sequence >= 1", name="ck_invoice_schedules_next_sequence"),
        sa.CheckConstraint(
            "payment_terms_days IS NULL OR payment_terms_days >= 0",
            name="ck_invoice_schedules_payment_terms",
        ),
    )
    op.create_index(
        "ix_invoice_schedules_workspace_id", "invoice_schedules", ["workspace_id"]
    )
    op.create_index("ix_invoice_schedules_payee_id", "invoice_schedules", ["payee_id"])
    op.create_index(
        "ix_invoice_schedules_workspace_status",
        "invoice_schedules",
        ["workspace_id", "status"],
    )

    op.create_table(
        "invoice_schedule_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("discount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["invoice_schedules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id", "effective_from", name="uq_invoice_schedule_terms_effective_from"
        ),
        sa.CheckConstraint("discount >= 0", name="ck_invoice_schedule_terms_discount"),
        sa.CheckConstraint("total >= 0", name="ck_invoice_schedule_terms_total"),
    )
    op.create_index(
        "ix_invoice_schedule_terms_schedule", "invoice_schedule_terms", ["schedule_id"]
    )
    op.create_index(
        "ix_invoice_schedule_terms_workspace_id", "invoice_schedule_terms", ["workspace_id"]
    )

    op.add_column(
        "invoices", sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("invoices", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("invoices", sa.Column("period_end", sa.Date(), nullable=True))
    op.create_foreign_key(
        "fk_invoices_schedule_id",
        "invoices",
        "invoice_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_invoices_schedule_id", "invoices", ["schedule_id"])
    op.create_unique_constraint(
        "uq_invoices_schedule_sequence", "invoices", ["schedule_id", "sequence"]
    )
    op.create_check_constraint(
        "ck_invoices_schedule_fields",
        "invoices",
        "(schedule_id IS NULL AND sequence IS NULL"
        " AND period_start IS NULL AND period_end IS NULL)"
        " OR (schedule_id IS NOT NULL AND sequence IS NOT NULL"
        " AND period_start IS NOT NULL AND period_end IS NOT NULL)",
    )


def downgrade() -> None:
    # The invoices stay: they are money, and only the memory of which
    # agreement they answered for is lost.
    op.drop_constraint("ck_invoices_schedule_fields", "invoices", type_="check")
    op.drop_constraint("uq_invoices_schedule_sequence", "invoices", type_="unique")
    op.drop_index("ix_invoices_schedule_id", table_name="invoices")
    op.drop_constraint("fk_invoices_schedule_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "period_end")
    op.drop_column("invoices", "period_start")
    op.drop_column("invoices", "sequence")
    op.drop_column("invoices", "schedule_id")

    op.drop_index("ix_invoice_schedule_terms_workspace_id", table_name="invoice_schedule_terms")
    op.drop_index("ix_invoice_schedule_terms_schedule", table_name="invoice_schedule_terms")
    op.drop_table("invoice_schedule_terms")

    op.drop_index("ix_invoice_schedules_workspace_status", table_name="invoice_schedules")
    op.drop_index("ix_invoice_schedules_payee_id", table_name="invoice_schedules")
    op.drop_index("ix_invoice_schedules_workspace_id", table_name="invoice_schedules")
    op.drop_table("invoice_schedules")
