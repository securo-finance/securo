"""add minimum_payment, card_brand, card_level to accounts

Revision ID: 026
Revises: 025
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("minimum_payment", sa.Numeric(precision=15, scale=2), nullable=True)
    )
    op.add_column("accounts", sa.Column("card_brand", sa.String(length=50), nullable=True))
    op.add_column("accounts", sa.Column("card_level", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "card_level")
    op.drop_column("accounts", "card_brand")
    op.drop_column("accounts", "minimum_payment")
