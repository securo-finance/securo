"""add asset_groups (wallets) and asset.group_id; backfill per connection

Revision ID: 034
Revises: 033
Create Date: 2026-04-20

Introduces user-defined "wallets" that bundle related assets under one
total. Synced assets get auto-grouped by their provider connection so
brokerages with many holdings collapse into a single expandable row
instead of many sibling cards.

Existing data is migrated: for every bank connection that has at least
one synced asset, a group is created (named after the institution) and
its assets are linked into it. Manual assets stay ungrouped unless the
user moves them in themselves.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_groups",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("icon", sa.String(50), nullable=False, server_default="wallet"),
        sa.Column("color", sa.String(7), nullable=False, server_default="#0EA5E9"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column(
            "connection_id",
            sa.UUID(),
            sa.ForeignKey("bank_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_asset_groups_user_id", "asset_groups", ["user_id"])
    op.create_index(
        "ux_asset_groups_user_source_external",
        "asset_groups",
        ["user_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.add_column(
        "assets",
        sa.Column(
            "group_id",
            sa.UUID(),
            sa.ForeignKey("asset_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_assets_group_id", "assets", ["group_id"])

    # Backfill: one group per connection that has synced assets. Named
    # after the institution; positioned after any existing groups. We
    # re-use the provider key (connection.external_id = Pluggy item id)
    # as the group's external_id so subsequent syncs upsert it in place.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO asset_groups
                (id, user_id, name, icon, color, position, source, connection_id, external_id)
            SELECT
                gen_random_uuid(),
                bc.user_id,
                bc.institution_name,
                'wallet',
                '#0EA5E9',
                ROW_NUMBER() OVER (PARTITION BY bc.user_id ORDER BY bc.created_at) - 1,
                bc.provider,
                bc.id,
                bc.external_id
            FROM bank_connections bc
            WHERE EXISTS (
                SELECT 1 FROM assets a
                WHERE a.connection_id = bc.id
                  AND a.source != 'manual'
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE assets a
            SET group_id = g.id
            FROM asset_groups g
            WHERE a.connection_id = g.connection_id
              AND a.user_id = g.user_id
              AND a.source = g.source
              AND a.group_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_assets_group_id", "assets")
    op.drop_column("assets", "group_id")
    op.drop_index("ux_asset_groups_user_source_external", "asset_groups")
    op.drop_index("ix_asset_groups_user_id", "asset_groups")
    op.drop_table("asset_groups")
