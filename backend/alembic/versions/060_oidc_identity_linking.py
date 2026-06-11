"""add oidc identity linking to users

Revision ID: 060
Revises: 059
Create Date: 2026-06-05

Re-chained from the original "055" to 060 to resolve a duplicate revision id:
the OIDC feature and the bank-connection-logo feature (#294) both shipped a
migration numbered 055, leaving two alembic heads so `alembic upgrade head`
failed on startup. This migration is additive (users.oidc_issuer/oidc_subject
+ indexes + unique constraint), so re-ordering it after the current head is
safe; it simply applies on the next upgrade.
"""

from alembic import op
import sqlalchemy as sa


revision = "060"
down_revision = "059"
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
