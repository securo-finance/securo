from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from app.models.account import Account
from app.models.asset_transaction import AssetTransaction
from app.models.bank_connection import BankConnection


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "066_trading212_connection_metadata.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("t212_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t212_metadata_migration_extends_065_with_only_additive_nullable_columns():
    migration = _migration_module()

    assert migration.revision == "066"
    assert migration.down_revision == "065"
    assert BankConnection.kind.property.columns[0].nullable is False
    assert Account.external_metadata.property.columns[0].nullable is True
    assert AssetTransaction.raw_data.property.columns[0].nullable is True


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
