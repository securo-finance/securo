"""add display_name to accounts

Revision ID: 031
Revises: 030
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("display_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "display_name")
