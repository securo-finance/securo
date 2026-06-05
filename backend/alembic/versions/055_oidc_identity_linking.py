"""add oidc identity linking to users

Revision ID: 055
Revises: 054
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_issuer", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("oidc_subject", sa.String(length=255), nullable=True))
    op.create_index("ix_users_oidc_issuer", "users", ["oidc_issuer"])
    op.create_index("ix_users_oidc_subject", "users", ["oidc_subject"])
    op.create_unique_constraint("uq_users_oidc_identity", "users", ["oidc_issuer", "oidc_subject"])


def downgrade() -> None:
    op.drop_constraint("uq_users_oidc_identity", "users", type_="unique")
    op.drop_index("ix_users_oidc_subject", table_name="users")
    op.drop_index("ix_users_oidc_issuer", table_name="users")
    op.drop_column("users", "oidc_subject")
    op.drop_column("users", "oidc_issuer")
