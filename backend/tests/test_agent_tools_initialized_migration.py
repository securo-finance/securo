import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_agent_tools_initialized.py")
_SPEC = importlib.util.spec_from_file_location("agent_tools_initialized_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def test_upgrade_marks_only_agents_with_tool_rows_as_initialized():
    """Agents with no rows ran with every tool; leaving them uninitialized
    makes their first discovery record every tool, so they keep that.
    Agents with rows keep exactly their rows."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE agents (id VARCHAR(36) PRIMARY KEY)"))
            conn.execute(sa.text(
                "CREATE TABLE agent_tools (agent_id VARCHAR(36), server VARCHAR(80), "
                "tool_name VARCHAR(120), enabled BOOLEAN)"
            ))
            conn.execute(sa.text("INSERT INTO agents (id) VALUES ('no-rows'), ('scoped')"))
            conn.execute(sa.text(
                "INSERT INTO agent_tools VALUES ('scoped', 'securo', 'list_accounts', 0)"
            ))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            rows = {
                agent_id: initialized
                for agent_id, initialized in conn.execute(
                    sa.text("SELECT id, tools_initialized FROM agents")
                )
            }
    finally:
        engine.dispose()
    assert rows == {"no-rows": 0, "scoped": 1}
