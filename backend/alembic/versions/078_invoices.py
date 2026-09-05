"""the invoicing ledger: invoices, lines, allocations and settings

Revision ID: 078
Revises: 077
Create Date: 2026-08-26

Four new tables, no change to any existing one. That is deliberate and
worth stating: the link between an invoice and the money that settles it
lives entirely in `invoice_allocations`, so `transactions` — the largest
and hottest table in every install — is not touched by this feature at
all. A foreign key there would also have been wrong on the merits: a
gateway payout is one bank credit settling many invoices, and a Pix-paid
invoice is one debt settled by several credits.

The status column holds only decisions a human took (`draft`, `open`,
`void`, `uncollectible`). Paid, partial and overdue are computed from
allocations and the due date at read time. Products that store `overdue`
need a nightly job to set it and compensating SQL to unset it when a due
date moves; there is nothing here for such a job to write.

Nothing in this migration is specific to a country. `document_type`
carries UNTDID's shape (invoice / credit note), `competence_date` is the
accrual date that Brazil calls competência, France fait générateur and
Germany requires on the invoice as Leistungsdatum, and the vocabulary
that fills any of it comes from the jurisdiction packs, never from DDL.

Downgrade drops all four tables. Safe by construction: nothing outside
them references them.

Numbering note: renumbered once already. This chains off `076`
(`payee_workspace_uniqueness`), which landed on `main` while this branch
was open. Any migration that lands before this one merges will need the
same two-line change plus a rename — nothing references the revision id
but the chain itself, which is why the CI check for it exists.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payee_id", postgresql.UUID(as_uuid=True), nullable=True),
        # UNTDID 1001: 380 invoice, 381 credit note. Only `invoice` is
        # accepted by the API today; the column exists now because adding
        # it later means backfilling every row in every self-hosted
        # install for a feature they were not using.
        sa.Column("document_type", sa.String(length=20), nullable=False, server_default="invoice"),
        # Which side of the ledger. Everything issued today is a
        # receivable; `payable` is here because supplier invoices are a
        # stated direction for this module, and adding the column later
        # means rewriting every row in every self-hosted install.
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="receivable"),
        sa.Column("corrects_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("external_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("series", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("competence_date", sa.Date(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("tax_total", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        # RESTRICT: deleting a client must never silently delete the
        # record of money they owed.
        sa.ForeignKeyConstraint(["payee_id"], ["payees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["corrects_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'void', 'uncollectible')", name="ck_invoices_status"
        ),
        sa.CheckConstraint(
            "document_type IN ('invoice', 'credit_note')", name="ck_invoices_document_type"
        ),
        sa.CheckConstraint(
            "direction IN ('receivable', 'payable')", name="ck_invoices_direction"
        ),
        sa.CheckConstraint("origin IN ('local', 'imported')", name="ck_invoices_origin"),
        # A draft has no number, and anything we issued has one. In the
        # database because the alternative is trusting every future code
        # path to remember. Imported rows are outside it: their identity
        # came with them, and a source that numbers nothing leaves the
        # column null rather than borrowing from our sequence.
        sa.CheckConstraint(
            "(status = 'draft' AND number IS NULL)"
            " OR (status <> 'draft' AND origin = 'local' AND number IS NOT NULL)"
            " OR origin = 'imported'",
            name="ck_invoices_number_matches_status",
        ),
        sa.CheckConstraint("total >= 0", name="ck_invoices_total_non_negative"),
        # Unique when present: SQL treats NULLs as distinct, so drafts
        # (which carry no number) are exempt for free.
        sa.UniqueConstraint(
            "workspace_id", "series", "number", name="uq_invoices_workspace_series_number"
        ),
        # Two syncs of the same external document converge on one row.
        sa.UniqueConstraint(
            "workspace_id", "external_source", "external_id", name="uq_invoices_workspace_external"
        ),
    )
    op.create_index("ix_invoices_workspace_id", "invoices", ["workspace_id"])
    op.create_index("ix_invoices_payee_id", "invoices", ["payee_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    # The read path: one workspace, one side of the ledger, open rows, by
    # due date. Every list and every aging bucket starts here.
    op.create_index(
        "ix_invoices_workspace_direction_status_due",
        "invoices",
        ["workspace_id", "direction", "status", "due_date"],
    )

    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=15, scale=4), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        # A rate, not an amount, and nullable: most workspaces under the
        # tracking preset never fill it in.
        sa.Column("tax_rate", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("total", sa.Numeric(precision=15, scale=2), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity >= 0", name="ck_invoice_lines_quantity_non_negative"),
    )
    op.create_index("ix_invoice_lines_invoice", "invoice_lines", ["invoice_id"])
    op.create_index("ix_invoice_lines_workspace_id", "invoice_lines", ["workspace_id"])

    op.create_table(
        "invoice_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("credit_note_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        # How the link came to exist: `manual`, or the id of the matching
        # strategy that produced the row. Sized for a strategy id rather
        # than an enum value, and left without a CHECK on purpose — those
        # ids come from a policy document the user will edit, so
        # constraining them here would mean a migration per strategy.
        sa.Column("method", sa.String(length=60), nullable=False, server_default="manual"),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        # CASCADE here and nowhere else in this migration: if the
        # transaction is gone the money it represented is gone, and a
        # link pointing at nothing would overstate what was received.
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_note_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_invoice_allocations_amount_positive"),
        # Exactly one source. A row settling from both a transaction and
        # a credit note is not richer, it is ambiguous.
        sa.CheckConstraint(
            "(transaction_id IS NOT NULL AND credit_note_id IS NULL)"
            " OR (transaction_id IS NULL AND credit_note_id IS NOT NULL)",
            name="ck_invoice_allocations_one_source",
        ),
        # The same transaction is never applied twice to the same
        # invoice. Two genuine partial payments are two transactions.
        sa.UniqueConstraint(
            "invoice_id", "transaction_id", name="uq_invoice_allocation_transaction"
        ),
    )
    op.create_index("ix_invoice_allocations_invoice", "invoice_allocations", ["invoice_id"])
    op.create_index("ix_invoice_allocations_transaction", "invoice_allocations", ["transaction_id"])
    op.create_index("ix_invoice_allocations_workspace_id", "invoice_allocations", ["workspace_id"])

    op.create_table(
        "invoice_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        # `tracking` (the document was issued elsewhere, or there is
        # none) vs `document` (the invoice is the deliverable). A
        # starting point that fills the next three fields, each
        # overridable afterwards — never a mode, and never a branch on
        # the UI language.
        sa.Column("preset", sa.String(length=20), nullable=False, server_default="tracking"),
        sa.Column("document_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("initial_state", sa.String(length=10), nullable=False, server_default="open"),
        sa.Column("tax_fields", sa.String(length=10), nullable=False, server_default="hidden"),
        sa.Column("default_payment_terms_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("number_prefix", sa.String(length=20), nullable=True),
        sa.Column("series", sa.String(length=20), nullable=True),
        # Consumed at issuance and never rolled back — not even when an
        # invoice is voided a second later. A reused number would put two
        # documents under one identifier.
        sa.Column("next_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("logo_url", sa.String(length=1000), nullable=True),
        sa.Column("issuer_display_name", sa.String(length=255), nullable=True),
        sa.Column("footer_note", sa.Text(), nullable=True),
        # Labels and custom-field definitions in one document. Peers ship
        # a column per label and pay a migration every time a label is
        # added; a workspace defining a PO-number field here pays none.
        sa.Column("template", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_invoice_settings_workspace"),
        sa.CheckConstraint("preset IN ('tracking', 'document')", name="ck_invoice_settings_preset"),
        sa.CheckConstraint(
            "initial_state IN ('draft', 'open')", name="ck_invoice_settings_initial_state"
        ),
        sa.CheckConstraint(
            "tax_fields IN ('hidden', 'optional', 'required')", name="ck_invoice_settings_tax_fields"
        ),
    )
    op.create_index("ix_invoice_settings_workspace_id", "invoice_settings", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("invoice_settings")
    op.drop_table("invoice_allocations")
    op.drop_table("invoice_lines")
    op.drop_table("invoices")
