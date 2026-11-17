import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


# Found by name rather than by number, so renumbering the migration to land
# after another one does not break this test.
(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_asset_transaction_splits.py")
_SPEC = importlib.util.spec_from_file_location("asset_transaction_splits_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest.fixture
def ledger(monkeypatch):
    # SQLite can't drop a constraint on its own. The columns still go for real.
    monkeypatch.setattr(migration.op, "drop_constraint", Mock())
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE asset_transactions "
                "(kind VARCHAR(10) NOT NULL, old_units INTEGER, new_units INTEGER)"
            ))
            with Operations.context(MigrationContext.configure(conn)):
                yield conn
    finally:
        # CI runs with -W error: a connection left to the garbage collector
        # raises ResourceWarning in whichever test happens to be running.
        engine.dispose()


def _columns(conn: sa.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(conn).get_columns("asset_transactions")}


def _kinds(conn: sa.Connection) -> list[str]:
    rows = conn.execute(sa.text("SELECT kind FROM asset_transactions ORDER BY kind"))
    return list(rows.scalars())


def test_downgrade_refuses_while_splits_exist_and_changes_nothing(ledger):
    ledger.execute(sa.text(
        "INSERT INTO asset_transactions VALUES ('buy', NULL, NULL), ('split', 1, 100)"
    ))

    with pytest.raises(RuntimeError, match="split entries exist"):
        migration.downgrade()

    assert _kinds(ledger) == ["buy", "split"]
    assert _columns(ledger) == {"kind", "old_units", "new_units"}
    migration.op.drop_constraint.assert_not_called()


def test_downgrade_drops_the_columns_once_no_split_is_left(ledger):
    ledger.execute(sa.text("INSERT INTO asset_transactions VALUES ('buy', NULL, NULL)"))

    migration.downgrade()

    assert _kinds(ledger) == ["buy"]
    assert _columns(ledger) == {"kind"}
    migration.op.drop_constraint.assert_called_once()
