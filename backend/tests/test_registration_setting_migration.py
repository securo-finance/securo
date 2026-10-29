import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


# Found by name rather than by number, so renumbering the migration to land
# after another one does not break this test.
(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_registration_setting_follows_env.py")
_SPEC = importlib.util.spec_from_file_location("registration_setting_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _run(direction: str, saved: str | None) -> str | None:
    """Run the migration over an app_settings table holding `saved`, and
    return what registration_enabled holds afterwards."""
    engine = sa.create_engine("sqlite://")
    try:
        return _run_on(engine, direction, saved)
    finally:
        # CI runs with -W error: a connection left to the garbage collector
        # raises ResourceWarning in whichever test happens to be running.
        engine.dispose()


def _run_on(engine: sa.Engine, direction: str, saved: str | None) -> str | None:
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE app_settings (key VARCHAR(255) PRIMARY KEY, value VARCHAR(2000) NOT NULL)"
        ))
        conn.execute(sa.text("INSERT INTO app_settings (key, value) VALUES ('timezone', 'UTC')"))
        if saved is not None:
            conn.execute(
                sa.text("INSERT INTO app_settings (key, value) VALUES ('registration_enabled', :v)"),
                {"v": saved},
            )
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()
        timezone = conn.execute(
            sa.text("SELECT value FROM app_settings WHERE key = 'timezone'")
        ).scalar_one()
        assert timezone == "UTC"
        return conn.execute(
            sa.text("SELECT value FROM app_settings WHERE key = 'registration_enabled'")
        ).scalar_one_or_none()


def test_upgrade_drops_the_seeded_true_so_the_env_var_decides():
    assert _run("upgrade", "true") is None


def test_upgrade_keeps_a_false_an_administrator_saved():
    assert _run("upgrade", "false") == "false"


@pytest.mark.parametrize(("saved", "after"), [(None, "true"), ("false", "false"), ("true", "true")])
def test_downgrade_restores_the_seed_only_where_nothing_is_saved(saved, after):
    assert _run("downgrade", saved) == after
