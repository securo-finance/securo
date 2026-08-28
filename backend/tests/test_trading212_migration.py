from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.asset_transaction import AssetTransaction
from app.models.bank_connection import BankConnection
from app.core.migration_safety import reject_ambiguous_legacy_063


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "077_trading212_connection_metadata.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("t212_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t212_metadata_migration_extends_076_with_only_additive_columns():
    migration = _migration_module()

    assert migration.revision == "077"
    assert migration.down_revision == "076"
    assert BankConnection.kind.property.columns[0].nullable is False
    assert Account.external_metadata.property.columns[0].nullable is True
    assert AssetTransaction.raw_data.property.columns[0].nullable is True


def test_t212_metadata_migration_declares_a_unique_broker_fill_identity():
    """A broker fill has one stable identity within its asset ledger."""
    table = AssetTransaction.__table__
    assert isinstance(table, sa.Table)
    indexes = table.indexes
    assert any(
        index.unique and {column.name for column in index.columns} == {"asset_id", "external_id"}
        for index in indexes
    )


def test_t212_metadata_migration_declares_a_unique_connection_identity():
    """One broker account may be represented only once per workspace."""
    table = BankConnection.__table__
    assert isinstance(table, sa.Table)
    assert any(
        index.name == "uq_bank_connections_t212_workspace_external_id"
        and index.unique
        and {column.name for column in index.columns}
        == {"workspace_id", "provider", "external_id"}
        and str(index.dialect_options["postgresql"]["where"])
        == "provider = 'trading212'"
        and str(index.dialect_options["sqlite"]["where"])
        == "provider = 'trading212'"
        for index in table.indexes
    )


def test_t212_metadata_migration_refuses_a_lossy_downgrade():
    """Retaining columns while stamping 076 would leave Alembic lying about schema."""
    migration = _migration_module()
    with __import__("pytest").raises(RuntimeError, match="cannot safely downgrade"):
        migration.downgrade()


def test_legacy_063_stamp_without_upstream_goal_column_fails_preflight():
    """The former T212 repair's 063 must never be accepted as upstream 063."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(sa.text("INSERT INTO alembic_version VALUES ('063')"))
        connection.execute(sa.text("CREATE TABLE goals (id INTEGER PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="ambiguous legacy Alembic revision 063"):
            reject_ambiguous_legacy_063(connection)


def test_t212_metadata_migration_skips_columns_already_created_by_a_legacy_installation():
    migration = _migration_module()

    class Inspector:
        def get_columns(self, table_name: str):
            return [{"name": name} for name in {
                "bank_connections": ["kind"],
                "accounts": ["external_metadata"],
                "asset_transactions": ["raw_data"],
            }[table_name]]

    with (
        patch.object(migration.op, "get_bind", return_value=object()),
        patch.object(migration.sa, "inspect", return_value=Inspector()),
        patch.object(migration.op, "add_column") as add_column,
    ):
        migration.upgrade()

    add_column.assert_not_called()


def test_t212_metadata_migration_adds_and_retains_columns_in_a_real_legacy_schema():
    """A failed downgrade must not drop legacy-compatible columns."""
    migration = _migration_module()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        for table in ("bank_connections", "accounts", "asset_transactions"):
            connection.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        inspector = sa.inspect(connection)
        assert {column["name"] for column in inspector.get_columns("bank_connections")} >= {"id", "kind"}
        assert {column["name"] for column in inspector.get_columns("accounts")} >= {
            "id",
            "external_metadata",
        }
        assert {column["name"] for column in inspector.get_columns("asset_transactions")} >= {
            "id",
            "raw_data",
        }

        with Operations.context(context), pytest.raises(RuntimeError, match="cannot safely downgrade"):
            migration.downgrade()

        inspector = sa.inspect(connection)
        assert {column["name"] for column in inspector.get_columns("bank_connections")} >= {"id", "kind"}
        assert {column["name"] for column in inspector.get_columns("accounts")} >= {
            "id",
            "external_metadata",
        }
        assert {column["name"] for column in inspector.get_columns("asset_transactions")} >= {
            "id",
            "raw_data",
        }


def test_t212_metadata_migration_enforces_connection_identity_without_affecting_other_providers():
    """The partial index blocks duplicate T212 accounts but preserves generic behavior."""
    migration = _migration_module()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """CREATE TABLE bank_connections (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    external_id TEXT NOT NULL
                )"""
            )
        )
        connection.execute(sa.text("CREATE TABLE accounts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE asset_transactions (id INTEGER PRIMARY KEY)"))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("bank_connections")}
        assert "uq_bank_connections_t212_workspace_external_id" in indexes

        connection.execute(
            sa.text(
                """INSERT INTO bank_connections
                (id, workspace_id, provider, external_id, kind)
                VALUES (1, 'workspace-1', 'trading212', 'account-1', 'brokerage')"""
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO bank_connections
                    (id, workspace_id, provider, external_id, kind)
                    VALUES (2, 'workspace-1', 'trading212', 'account-1', 'brokerage')"""
                )
            )

        connection.execute(
            sa.text(
                """INSERT INTO bank_connections
                (id, workspace_id, provider, external_id, kind)
                VALUES
                  (3, 'workspace-1', 'pluggy', 'item-1', 'banking'),
                  (4, 'workspace-1', 'pluggy', 'item-1', 'banking')"""
            )
        )


def test_t212_metadata_migration_refuses_to_delete_duplicate_connections():
    """Legacy duplicate connections require manual reconciliation, never data loss."""
    migration = _migration_module()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """CREATE TABLE bank_connections (
                    id INTEGER PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    external_id TEXT NOT NULL
                )"""
            )
        )
        connection.execute(sa.text("CREATE TABLE accounts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE asset_transactions (id INTEGER PRIMARY KEY)"))
        connection.execute(
            sa.text(
                """INSERT INTO bank_connections
                (id, workspace_id, provider, external_id)
                VALUES
                  (1, 'workspace-1', 'trading212', 'account-1'),
                  (2, 'workspace-1', 'trading212', 'account-1')"""
            )
        )

        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(
            RuntimeError, match="duplicate accounts exist"
        ):
            migration.upgrade()

        count = connection.scalar(sa.text("SELECT COUNT(*) FROM bank_connections"))
        assert count == 2
