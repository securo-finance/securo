"""add additive Trading 212 connection and provider metadata

Revision ID: 077
Revises: 076
Create Date: 2026-08-03

The columns are generic provider metadata rather than Trading 212-specific
schema.  Some local installations previously carried equivalent columns from an
unreleased connector branch.  Each addition is therefore inspected first and
skipped when present: upgrading from an ordinary 076 database and upgrading a
legacy local database are both non-destructive.  No data is renamed, rewritten,
or dropped. Partial unique indexes give both a Trading 212 connection and each
provider fill a database-enforced identity; legacy fill duplicates are removed
deterministically before the fill index is created, while duplicate connections
fail closed for manual reconciliation rather than deleting financial history.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def _index_exists(table: str, name: str) -> bool:
    return any(
        item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes(table)
    )


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

    connection_index = "uq_bank_connections_t212_workspace_external_id"
    if _has_columns(
        "bank_connections", {"workspace_id", "provider", "external_id"}
    ) and not _index_exists("bank_connections", connection_index):
        duplicate = op.get_bind().execute(
            sa.text(
                """SELECT workspace_id, external_id
                FROM bank_connections
                WHERE provider = 'trading212'
                GROUP BY workspace_id, external_id
                HAVING COUNT(*) > 1
                LIMIT 1"""
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                "cannot enforce unique Trading 212 connection identity: "
                "duplicate accounts exist in one workspace; remove or reconcile "
                "the duplicate connection before retrying migration 077"
            )
        op.create_index(
            connection_index,
            "bank_connections",
            ["workspace_id", "provider", "external_id"],
            unique=True,
            postgresql_where=sa.text("provider = 'trading212'"),
            sqlite_where=sa.text("provider = 'trading212'"),
        )

    if _has_columns(
        "asset_transactions", {"asset_id", "external_id"}
    ) and not _index_exists(
        "asset_transactions", "uq_asset_transactions_asset_external_id"
    ):
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
    # unreleased local connector work.  A no-op would let Alembic stamp 076
    # while keeping 077 schema, which is worse than a clear failed rollback.
    raise RuntimeError(
        "cannot safely downgrade Trading 212 metadata migration: schema ownership is ambiguous; "
        "restore a backup or keep revision 077"
    )
