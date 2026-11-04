import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "077_asset_external_id_workspace.py"
)
_SPEC = importlib.util.spec_from_file_location("asset_external_id_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


@pytest.mark.parametrize(
    ("direction", "scope_column"),
    [("upgrade", "workspace_id"), ("downgrade", "user_id")],
)
def test_index_change_aborts_before_schema_changes(monkeypatch, direction, scope_column):
    bind = Mock()
    bind.execute.return_value = _Result(
        {
            "scope_id": "scope-1",
            "source": "provider",
            "external_id": "asset-1",
            "row_count": 2,
        }
    )
    drop_index = Mock()
    create_index = Mock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "drop_index", drop_index)
    monkeypatch.setattr(migration.op, "create_index", create_index)

    with pytest.raises(RuntimeError, match=f"{scope_column}=scope-1"):
        getattr(migration, direction)()

    statement = " ".join(str(bind.execute.call_args.args[0]).lower().split())
    assert f"select {scope_column} as scope_id" in statement
    assert f"group by {scope_column}, source, external_id" in statement
    drop_index.assert_not_called()
    create_index.assert_not_called()
