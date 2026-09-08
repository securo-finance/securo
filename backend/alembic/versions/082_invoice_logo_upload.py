"""the logo becomes a file this workspace owns

A URL fetched the mark from a third party every time a document was
drawn, and on a self-hosted install that is a request out of the building
for an image the user already has. It is uploaded now, and the column
holds the id the storage key is derived from.

`logo_url` is dropped rather than migrated: it was introduced in this
same series and has never been in a release, so nothing in the world
holds one.

Revision ID: 082
Revises: 081
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_settings",
        sa.Column("logo_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_column("invoice_settings", "logo_url")


def downgrade() -> None:
    op.add_column(
        "invoice_settings", sa.Column("logo_url", sa.String(length=1000), nullable=True)
    )
    op.drop_column("invoice_settings", "logo_id")
