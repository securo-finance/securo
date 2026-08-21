"""model institutions as first-class rows under a connection (issue #345)

Revision ID: 073
Revises: 072
Create Date: 2026-08-21

One SimpleFIN connection can span multiple institutions, but Securo applied
the connection's single institution_name/logo_url to every account under it.
A connection now has institutions, and each account points at its own.

Additive and nullable: existing rows keep working unchanged (serialization
falls back to the connection) and institution rows appear on the next sync.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bank_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("logo_url", sa.String(500), nullable=True),
        # Also serves as the connection_id lookup index, and keeps two syncs
        # racing on the same payload from double-inserting an institution.
        sa.UniqueConstraint("connection_id", "name", name="uq_institutions_connection_name"),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "institution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("institutions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Synced wallets are per investment account, so they carry the backing
    # institution for their "Synced from …" subtitle.
    op.add_column(
        "asset_groups",
        sa.Column(
            "institution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("institutions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("asset_groups", "institution_id")
    op.drop_column("accounts", "institution_id")
    op.drop_table("institutions")
