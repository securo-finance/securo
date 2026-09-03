import importlib.util
from pathlib import Path
from unittest.mock import Mock

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "086_rename_accounting_mode_values.py"
)
_SPEC = importlib.util.spec_from_file_location("accounting_mode_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _statements(execute_mock):
    return [
        " ".join(
            str(call.args[0].compile(compile_kwargs={"literal_binds": True})).lower().split()
        )
        for call in execute_mock.call_args_list
    ]


def test_upgrade_remaps_cash_and_accrual_preserving_behavior(monkeypatch):
    execute = Mock()
    monkeypatch.setattr(migration.op, "execute", execute)

    migration.upgrade()

    statements = _statements(execute)
    assert len(statements) == 2
    assert "value='purchase_date'" in statements[0]
    assert "key = 'credit_card_accounting_mode'" in statements[0]
    assert "value = 'cash'" in statements[0]
    assert "value='invoice_due_date'" in statements[1]
    assert "value = 'accrual'" in statements[1]


def test_downgrade_reverses_the_rename(monkeypatch):
    execute = Mock()
    monkeypatch.setattr(migration.op, "execute", execute)

    migration.downgrade()

    statements = _statements(execute)
    assert len(statements) == 2
    assert "value='cash'" in statements[0]
    assert "value = 'purchase_date'" in statements[0]
    assert "value='accrual'" in statements[1]
    assert "value = 'invoice_due_date'" in statements[1]
