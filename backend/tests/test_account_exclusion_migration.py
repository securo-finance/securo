"""Exercise the additive account-exclusion migration on disposable SQLite state.

This is a portability/DDL check of this migration, not a PostgreSQL full-chain
upgrade. PostgreSQL interprets the quoted boolean default as false; SQLite
stores the literal string, so inspect its raw value rather than ORM coercion.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_account_exclude_from_reports.py")
_SPEC = importlib.util.spec_from_file_location("account_exclusion_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def test_account_exclusion_migration_preserves_rows_and_roundtrips():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(
                "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            ))
            connection.execute(sa.text("INSERT INTO accounts VALUES (1, 'Existing account')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("accounts")}
            assert columns["exclude_from_reports"]["nullable"] is False
            assert columns["exclude_from_reports"]["default"] == "'false'"
            assert connection.execute(sa.text(
                "SELECT id, name, exclude_from_reports FROM accounts"
            )).all() == [(1, "Existing account", "false")]
            connection.execute(sa.text(
                "INSERT INTO accounts (id, name) VALUES (2, 'New account')"
            ))
            assert connection.execute(sa.text(
                "SELECT exclude_from_reports FROM accounts WHERE id = 2"
            )).scalar_one() == "false"
            connection.execute(sa.text(
                "UPDATE accounts SET exclude_from_reports = true WHERE id = 1"
            ))
            assert connection.execute(sa.text(
                "SELECT exclude_from_reports FROM accounts WHERE id = 1"
            )).scalar_one() == 1
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "exclude_from_reports" not in {
                column["name"] for column in sa.inspect(connection).get_columns("accounts")
            }
            assert connection.execute(sa.text(
                "SELECT id, name FROM accounts ORDER BY id"
            )).all() == [(1, "Existing account"), (2, "New account")]
    finally:
        engine.dispose()
