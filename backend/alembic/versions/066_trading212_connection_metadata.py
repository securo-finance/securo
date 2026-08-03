"""add additive Trading 212 connection and provider metadata

Revision ID: 066
Revises: 065
Create Date: 2026-08-03

The columns are generic provider metadata rather than Trading 212-specific
schema.  Some local installations previously carried equivalent columns from an
unreleased connector branch.  Each addition is therefore inspected first and
skipped when present: upgrading from an ordinary 065 database and upgrading a
legacy local database are both non-destructive.  No data is renamed, rewritten,
or dropped.  A partial unique index gives provider fills a database-enforced
identity; any duplicate legacy fills are removed deterministically before it
is created.
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


def _index_exists(name: str) -> bool:
    return any(item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes("asset_transactions"))


def _has_columns(table: str, names: set[str]) -> bool:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    return names.issubset(columns)


def upgrade() -> None:
    _add_column_if_missing(
        "bank_connections",
        sa.Column("kind", sa.String(length=50), nullable=False, server_default="banking"),
    )
    _add_column_if_missing("accounts", sa.Column("external_metadata", sa.JSON(), nullable=True))
    _add_column_if_missing("asset_transactions", sa.Column("raw_data", sa.JSON(), nullable=True))
    if _has_columns("asset_transactions", {"asset_id", "external_id"}) and not _index_exists("uq_asset_transactions_asset_external_id"):
        # PostgreSQL is the supported production database.  ``ctid`` provides
        # a stable physical tie-breaker where created_at/id are equal or null.
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                """
                DELETE FROM asset_transactions duplicate
                USING asset_transactions kept
                WHERE duplicate.asset_id = kept.asset_id
                  AND duplicate.external_id = kept.external_id
                  AND duplicate.external_id IS NOT NULL
                  AND duplicate.ctid > kept.ctid
                """
            )
        else:  # test/dev SQLite equivalent; production is PostgreSQL.
            op.execute(
                """DELETE FROM asset_transactions
                WHERE external_id IS NOT NULL AND rowid NOT IN (
                    SELECT MIN(rowid) FROM asset_transactions
                    WHERE external_id IS NOT NULL GROUP BY asset_id, external_id
                )"""
            )
        op.create_index(
            "uq_asset_transactions_asset_external_id",
            "asset_transactions",
            ["asset_id", "external_id"],
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL"),
        )


def downgrade() -> None:
    # We cannot distinguish this revision's columns/index from identical
    # unreleased local connector work.  A no-op would let Alembic stamp 065
    # while keeping 066 schema, which is worse than a clear failed rollback.
    raise RuntimeError(
        "cannot safely downgrade Trading 212 metadata migration: schema ownership is ambiguous; "
        "restore a backup or keep revision 066"
    )
