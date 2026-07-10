"""add sync fields to assets for provider-agnostic investment holdings

Revision ID: 032
Revises: 031
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "connection_id",
            sa.UUID(),
            sa.ForeignKey("bank_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("assets", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column(
        "assets",
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
    )
    op.add_column("assets", sa.Column("isin", sa.String(20), nullable=True))
    op.add_column("assets", sa.Column("maturity_date", sa.Date(), nullable=True))
    op.add_column("assets", sa.Column("external_metadata", sa.JSON(), nullable=True))

    # Partial unique index: one synced asset per (user, source, external_id).
    # NULL external_ids (manual entries) are exempt so users can have many
    # manual assets with the same name.
    op.create_index(
        "ux_assets_user_source_external",
        "assets",
        ["user_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("ix_assets_connection_id", "assets", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_assets_connection_id", "assets")
    op.drop_index("ux_assets_user_source_external", "assets")
    op.drop_column("assets", "external_metadata")
    op.drop_column("assets", "maturity_date")
    op.drop_column("assets", "isin")
    op.drop_column("assets", "source")
    op.drop_column("assets", "external_id")
    op.drop_column("assets", "connection_id")
