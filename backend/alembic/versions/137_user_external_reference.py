"""the operator's own reference for an account it opened

A managed instance's operator opens accounts in one call (#1965) and names
each one with a reference of its choosing. Unique, so a retried call finds the
account it already opened instead of opening a second one. Null for every
account opened any other way.

Revision ID: 137
Revises: 136
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "137"
down_revision: Union[str, None] = "136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("external_reference", sa.String(255), nullable=True))
    op.create_index(
        "ix_users_external_reference", "users", ["external_reference"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_users_external_reference", table_name="users")
    op.drop_column("users", "external_reference")
