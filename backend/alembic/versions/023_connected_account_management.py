"""connected account management

Revision ID: 023_connected_account_management
Revises: 022
Create Date: 2026-04-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "023_connected_account_management"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("custom_name", sa.String(length=255), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("bill_import_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("accounts", "bill_import_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("accounts", "bill_import_enabled")
    op.drop_column("accounts", "custom_name")
