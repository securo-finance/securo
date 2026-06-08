"""add logo_url to bank_connections

Revision ID: 055
Revises: 054
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bank_connections", sa.Column("logo_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_connections", "logo_url")
