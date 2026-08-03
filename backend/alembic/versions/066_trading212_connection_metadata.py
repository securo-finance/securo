"""add additive Trading 212 connection and provider metadata

Revision ID: 066
Revises: 065
Create Date: 2026-08-03

The columns are generic provider metadata rather than Trading 212-specific
schema.  Some local installations previously carried equivalent columns from an
unreleased connector branch.  Each addition is therefore inspected first and
skipped when present: upgrading from an ordinary 065 database and upgrading a
legacy local database are both non-destructive.  No data is renamed, rewritten,
or dropped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing(
        "bank_connections",
        sa.Column("kind", sa.String(length=50), nullable=False, server_default="banking"),
    )
    _add_column_if_missing("accounts", sa.Column("external_metadata", sa.JSON(), nullable=True))
    _add_column_if_missing("asset_transactions", sa.Column("raw_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Intentionally a no-op. We cannot distinguish a column created by this
    # revision from one that was already present on a legacy local install, so
    # dropping would make a rollback destructive for the latter.
    pass
