"""where each filed document came from

An invoice is assembled from several systems: the bill from a payment
provider, the fiscal document from a government portal, a receipt
forwarded by email. Without recording which produced which, the folder is
a pile of files with no author and no way to recognise one already
collected when that system syncs again.

Revision ID: 081
Revises: 080
"""
from alembic import op
import sqlalchemy as sa

revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_attachments", sa.Column("source", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "invoice_attachments",
        sa.Column("external_id", sa.String(length=255), nullable=True),
    )
    # Partial: a null external id means "this system does not name its
    # files", not a value two rows can collide on, and a hand-uploaded
    # file has no source at all.
    op.create_index(
        "uq_invoice_attachments_external",
        "invoice_attachments",
        ["workspace_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invoice_attachments_external", table_name="invoice_attachments")
    op.drop_column("invoice_attachments", "external_id")
    op.drop_column("invoice_attachments", "source")
