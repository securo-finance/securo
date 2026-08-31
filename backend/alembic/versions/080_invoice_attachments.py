"""invoice attachments, and a name for an imported document

Two changes that belong together, because both say the same thing: an
invoice gathers paper it did not necessarily write.

  - `invoice_attachments` holds the files, each with the role it plays
    and at most one marked as *the* document.
  - `invoices.external_number` holds the name an imported document
    arrived with. It replaces the earlier attempt to store it in our own
    integer column, which could not hold `2026/A/0031` and quietly spent
    a number from a sequence that is supposed to be ours alone.

Revision ID: 080
Revises: 079
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("external_number", sa.String(length=60), nullable=True),
    )

    # Anything imported that took a number from our sequence gets its name
    # back and gives the number up. Reversing it in SQL rather than in a
    # later code path is what keeps installs that ran the previous
    # revision from carrying a document renamed into our numbering.
    op.execute(
        """
        UPDATE invoices
           SET external_number = COALESCE(series, '') || number::text,
               number = NULL,
               series = NULL
         WHERE origin = 'imported'
           AND number IS NOT NULL
        """
    )

    op.drop_constraint("ck_invoices_number_matches_status", "invoices", type_="check")
    op.create_check_constraint(
        "ck_invoices_number_matches_status",
        "invoices",
        "(origin = 'imported' AND number IS NULL)"
        " OR (status = 'draft' AND number IS NULL)"
        " OR (status <> 'draft' AND number IS NOT NULL)",
    )

    op.create_table(
        "invoice_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=20), server_default="other", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("document_number", sa.String(length=120), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('bill', 'fiscal', 'receipt', 'contract', 'other')",
            name="ck_invoice_attachments_kind",
        ),
    )
    op.create_index(
        "ix_invoice_attachments_invoice", "invoice_attachments", ["invoice_id"]
    )
    op.create_index(
        "ix_invoice_attachments_workspace_id", "invoice_attachments", ["workspace_id"]
    )
    # "Unique among the true ones": one file per invoice may be the
    # document, and any number of files may not be.
    op.create_index(
        "uq_invoice_attachments_primary",
        "invoice_attachments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    """Gives back what `upgrade` took, as far as it can be given back.

    `upgrade` folded `series || number` into one text column and cleared
    both. Splitting that back apart is guesswork in general — a source
    that numbered a document `2026/A/0031` leaves nothing to say where
    the series ended — so only the unambiguous case is restored: a value
    that is digits and nothing else goes back into `number`.

    Anything else stays in `external_number` until the column is dropped
    on the next statement, and is then gone. On a self-hosted install
    that is the only copy of the name the source gave the document, which
    is why it is said here rather than discovered afterwards.
    """
    op.drop_index("uq_invoice_attachments_primary", table_name="invoice_attachments")
    op.drop_index("ix_invoice_attachments_workspace_id", table_name="invoice_attachments")
    op.drop_index("ix_invoice_attachments_invoice", table_name="invoice_attachments")
    op.drop_table("invoice_attachments")

    op.drop_constraint("ck_invoices_number_matches_status", "invoices", type_="check")
    op.create_check_constraint(
        "ck_invoices_number_matches_status",
        "invoices",
        "(status = 'draft' AND number IS NULL)"
        " OR (status <> 'draft' AND origin = 'local' AND number IS NOT NULL)"
        " OR origin = 'imported'",
    )

    # The reversible half: a purely numeric name is the same value the
    # column held before, so it goes home.
    op.execute(
        """
        UPDATE invoices
           SET number = external_number::integer
         WHERE origin = 'imported'
           AND external_number ~ '^[0-9]+$'
        """
    )
    op.drop_column("invoices", "external_number")
