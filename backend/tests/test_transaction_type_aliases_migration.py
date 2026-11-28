import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

# Found by name rather than by number, so renumbering the migration to land
# after another one does not break this test.
(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_transaction_type_aliases.py")
_SPEC = importlib.util.spec_from_file_location("transaction_type_aliases_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)

STORED = ["debit", "credit", "expense", "income", " Expense ", "INCOME", "transfer", "\tExpense\n", "\r\nIncome\t"]


def _upgrade_over(stored: list[str]) -> dict[str, list[str]]:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            for table in migration.TABLES:
                conn.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, type VARCHAR(20))"))
                for i, value in enumerate(stored):
                    conn.execute(sa.text(f"INSERT INTO {table} (id, type) VALUES (:i, :t)"), {"i": i, "t": value})
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            return {
                table: list(conn.execute(sa.text(f"SELECT type FROM {table} ORDER BY id")).scalars())
                for table in migration.TABLES
            }
    finally:
        # CI runs with -W error: a connection left to the garbage collector
        # raises ResourceWarning in whichever test happens to be running.
        engine.dispose()


def test_upgrade_stores_the_aliases_as_debit_and_credit_in_both_tables():
    expected = ["debit", "credit", "debit", "credit", "debit", "credit", "transfer", "debit", "credit"]
    assert _upgrade_over(STORED) == {table: expected for table in migration.TABLES}
