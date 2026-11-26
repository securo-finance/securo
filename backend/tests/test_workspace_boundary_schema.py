"""Every table is inside a workspace, or says why it isn't (ADR 0002).

The workspace is the data boundary: financial rows carry ``workspace_id`` and
every query is scoped by it. A new table that forgets the column is invisible
to review (the model looks fine, the tests pass on one workspace) and leaks
the first time someone has two. So this walks the whole metadata instead of
trusting memory.

A table without ``workspace_id`` must be listed below with the reason. The
reason is checked where it can be: a table scoped through its parent must
really hang off a workspace-owned parent, and a table owned by a user must
really point at one.
"""
from sqlalchemy import Table, UniqueConstraint

from app.core.database import Base
from app.models import *  # noqa: F401,F403
from app.agents.models import *  # noqa: F401,F403

#: Tables outside any workspace on purpose.
GLOBAL: dict[str, str] = {
    "workspaces": "the boundary itself",
    "app_settings": "instance-wide settings, written by admins",
    "fx_rates": "exchange rates are public facts, shared by every workspace (ADR 0008)",
    "plugin_logos": "images from a public plugin release, kept per commit and shared by every workspace that runs it",
    "admin_access_log": "an instance-wide record of administrative access to accounts, readable by the account and by workspace owners (ADR 0042)",
    "admin_access_log_viewer": "who may read one entry of admin_access_log, recorded with it so it survives the account (ADR 0042)",
}

#: Tables owned by a user across workspaces: their credentials and their own AI keys.
USER_OWNED: dict[str, str] = {
    "users": "the account itself",
    "user_passkeys": "a user's sign-in credentials",
    "agent_llm_connections": "a user's own LLM provider keys",
    "agent_llm_usage": "a user's LLM usage log; outlives the agent or conversation",
}

#: Tables scoped by a parent that has ``workspace_id``: table -> FK column.
VIA_PARENT: dict[str, str] = {
    "agent_knowledge_docs": "agent_id",
    "agent_knowledge_chunks": "agent_id",
    "agent_messages": "conversation_id",
    "agent_tools": "agent_id",
    "collection_accounts": "collection_id",
    "collection_asset_groups": "collection_id",
    "institutions": "connection_id",
    "plugin_connection_routines": "connection_id",
}

EXEMPT = GLOBAL.keys() | USER_OWNED.keys() | VIA_PARENT.keys()


def _tables() -> dict[str, Table]:
    return dict(Base.metadata.tables)


def test_every_table_has_workspace_id_or_a_reason():
    missing = sorted(
        name for name, table in _tables().items()
        if "workspace_id" not in table.c and name not in EXEMPT
    )
    assert not missing, (
        f"Tables without workspace_id: {missing}. Add the column (ADR 0002), or list the "
        "table in GLOBAL, USER_OWNED or VIA_PARENT here with the reason."
    )


def test_exemptions_are_not_stale():
    tables = _tables()
    unknown = sorted(name for name in EXEMPT if name not in tables)
    assert not unknown, f"Exempted tables that no longer exist: {unknown}"
    scoped = sorted(name for name in EXEMPT if "workspace_id" in tables[name].c)
    assert not scoped, f"Exempted tables that now have workspace_id, drop the exemption: {scoped}"


def test_workspace_id_is_required_indexed_and_cascades():
    problems = []
    for name, table in sorted(_tables().items()):
        column = table.c.get("workspace_id")
        if column is None:
            continue
        if column.nullable:
            problems.append(f"{name}: workspace_id is nullable")
        targets = [(fk.column.table.name, fk.column.name, fk.ondelete) for fk in column.foreign_keys]
        if targets != [("workspaces", "id", "CASCADE")]:
            problems.append(f"{name}: workspace_id must reference workspaces.id ON DELETE CASCADE, got {targets}")
        # A unique constraint builds an index too, so one that leads with the column counts.
        leading = [index.columns for index in table.indexes] + [
            constraint.columns for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        indexed = column.index or any(
            len(columns) and next(iter(columns)).name == "workspace_id" for columns in leading
        )
        if not indexed:
            problems.append(f"{name}: workspace_id does not lead any index or unique constraint")
    assert not problems, "\n".join(problems)


def test_parent_scoped_tables_hang_off_a_workspace_owned_parent():
    tables = _tables()
    problems = []
    for name, column_name in sorted(VIA_PARENT.items()):
        column = tables[name].c.get(column_name)
        if column is None:
            problems.append(f"{name}: no column {column_name}")
            continue
        fks = list(column.foreign_keys)
        if len(fks) != 1:
            problems.append(f"{name}.{column_name}: expected one foreign key, got {len(fks)}")
            continue
        fk = fks[0]
        if column.nullable:
            problems.append(f"{name}.{column_name}: nullable, so a row can escape its parent")
        if fk.ondelete != "CASCADE":
            problems.append(f"{name}.{column_name}: must cascade with its parent, got {fk.ondelete}")
        if "workspace_id" not in fk.column.table.c:
            problems.append(f"{name}.{column_name}: parent {fk.column.table.name} has no workspace_id")
    assert not problems, "\n".join(problems)


def test_user_owned_tables_point_at_a_user():
    tables = _tables()
    problems = []
    for name in sorted(USER_OWNED.keys() - {"users"}):
        column = tables[name].c.get("user_id")
        targets = [fk.column.table.name for fk in column.foreign_keys] if column is not None else []
        if column is None or column.nullable or targets != ["users"]:
            problems.append(f"{name}: needs a non-null user_id referencing users")
    assert not problems, "\n".join(problems)
