"""add collection <-> wallet (asset_group) membership

Revision ID: 057
Revises: 056
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_asset_groups",
        sa.Column("collection_id", UUID(as_uuid=True), nullable=False),
        sa.Column("asset_group_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_group_id"], ["asset_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collection_id", "asset_group_id"),
    )


def downgrade() -> None:
    op.drop_table("collection_asset_groups")
