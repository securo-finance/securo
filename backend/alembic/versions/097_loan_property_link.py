"""link loan accounts to the real-estate asset they are secured against

Revision ID: 097
Revises: 096
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("secured_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_accounts_secured_asset_id", "accounts", ["secured_asset_id"])
    op.create_foreign_key(
        "fk_accounts_secured_asset_id_assets",
        "accounts",
        "assets",
        ["secured_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_accounts_secured_asset_id_assets", "accounts", type_="foreignkey")
    op.drop_index("ix_accounts_secured_asset_id", table_name="accounts")
    op.drop_column("accounts", "secured_asset_id")
