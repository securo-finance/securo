"""The workspace boundary, enforced by Postgres as well (ADR 0043).

With ROW_SECURITY_ENABLED the app connects as a role that owns nothing and
can't bypass row security, and every transaction carries the workspace it works
for in `app.workspace_id`. The policies let through only that workspace's rows,
so a query that forgot its filter finds nothing instead of someone else's data.
Without a workspace, covered tables are empty.

Services commit in the middle of a request, which ends a transaction-local
setting, so the workspace lives on the session and is sent again each time a
transaction begins. Off, nothing here sends a statement.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection, Table, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.core.config import get_settings

_WORKSPACE = "row_security_workspace_id"
_USER = "row_security_user_id"

_SET = text(
    "SELECT set_config('app.workspace_id', :workspace, true), set_config('app.user_id', :user, true)"
)

#: Tables with ``workspace_id`` that have no policy yet, and why.
NOT_COVERED: dict[str, str] = {
    "workspace_members": "read to find out which workspace a request may enter",
    "access_tokens": "read by hash before any workspace is known",
    "groups": "shared with members in other workspaces",
    "group_members": "shared with members in other workspaces",
    "group_settlements": "shared with members in other workspaces",
    "transaction_splits": "shared with members in other workspaces",
}

#: Tables scoped through a parent that is covered: table -> (FK column, parent).
VIA_PARENT: dict[str, tuple[str, str]] = {
    "agent_knowledge_docs": ("agent_id", "agents"),
    "agent_knowledge_chunks": ("agent_id", "agents"),
    "agent_messages": ("conversation_id", "agent_conversations"),
    "agent_tools": ("agent_id", "agents"),
    "collection_accounts": ("collection_id", "collections"),
    "collection_asset_groups": ("collection_id", "collections"),
    "institutions": ("connection_id", "bank_connections"),
    "plugin_connection_routines": ("connection_id", "plugin_connections"),
}

#: Rows a job is given by id alone. `row_workspace` answers which workspace one
#: belongs to, and nothing else about it.
LOOKUP_TABLES = ("agents", "bank_connections", "document_extractions", "plugin_connections")


def enabled() -> bool:
    return get_settings().row_security_enabled


@event.listens_for(Session, "after_begin")
def _send_workspace(session: Session, _transaction: Any, connection: Connection) -> None:
    if _WORKSPACE not in session.info or connection.dialect.name != "postgresql":
        return
    connection.execute(_SET, _values(session.info))


def _values(info: dict) -> dict[str, str]:
    return {"workspace": str(info[_WORKSPACE]), "user": str(info.get(_USER) or "")}


async def bind_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> None:
    """Make this session work for one workspace from here on, including the
    transaction already open."""
    if not enabled():
        return
    info = session.sync_session.info
    info[_WORKSPACE] = workspace_id
    info[_USER] = user_id
    if session.in_transaction() and session.get_bind().dialect.name == "postgresql":
        await session.execute(_SET, _values(info))


def bound_to(session: AsyncSession) -> uuid.UUID | None:
    """The workspace this session works for, if it is bound to one."""
    return session.sync_session.info.get(_WORKSPACE)


def scoped_maker(session_maker: Any, workspace_id: uuid.UUID) -> async_sessionmaker:
    """The same session maker, with every session bound to one workspace."""
    options: dict[str, Any] = {**session_maker.kw, "info": {_WORKSPACE: workspace_id}}
    return async_sessionmaker(**options)


async def workspace_makers(session_maker: Any) -> list[Any]:
    """The session makers a job that covers every workspace runs through.

    Off, that is the one it has, and the job reads everything at once as it
    always did. On, there is one per workspace, so the job can only ever touch
    one workspace's rows at a time.
    """
    if not enabled():
        return [session_maker]
    from app.models.workspace import Workspace

    async with session_maker() as session:
        ids = (await session.scalars(select(Workspace.id).order_by(Workspace.id))).all()
    return [scoped_maker(session_maker, workspace_id) for workspace_id in ids]


async def maker_for_row(session_maker: Any, table: str, row_id: uuid.UUID) -> Any:
    """The session maker for a job that was given one row's id."""
    if not enabled():
        return session_maker
    if table not in LOOKUP_TABLES:
        raise ValueError(f"{table} is not a lookup table")
    async with session_maker() as session:
        workspace_id = await session.scalar(
            text("SELECT securo_row_workspace(:table, :id)"), {"table": table, "id": row_id}
        )
    # A row that is gone has no workspace: the job runs unbound and finds nothing.
    return session_maker if workspace_id is None else scoped_maker(session_maker, workspace_id)


# --- What the owner sets up ---------------------------------------------------

def covered_tables(tables: dict[str, Table]) -> list[str]:
    own = [n for n, t in tables.items() if "workspace_id" in t.c and n not in NOT_COVERED]
    return sorted(own + [n for n in VIA_PARENT if n in tables])


def setup_statements(tables: dict[str, Table], role: str, schema: str, quote: Any) -> list[str]:
    """Everything that makes `role` a restricted one. Safe to run again: it is
    run after every migration, so a new table is covered as it arrives."""
    sch, rol = quote(schema), quote(role)
    current = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
    viewer = "NULLIF(current_setting('app.user_id', true), '')::uuid"
    out = [
        f"GRANT USAGE ON SCHEMA {sch} TO {rol}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {sch} TO {rol}",
    ]
    for name in sorted(tables):
        out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {sch}.{quote(name)} TO {rol}")
    # Definer functions run as the owner, with a fixed search path, and answer
    # one narrow question each. Only the restricted role may call them.
    branches = " ".join(
        f"WHEN '{n}' THEN RETURN (SELECT workspace_id FROM {sch}.{quote(n)} WHERE id = p_id);"
        for n in LOOKUP_TABLES
    )
    out += [
        f"CREATE OR REPLACE FUNCTION {sch}.securo_row_workspace(p_table text, p_id uuid) RETURNS uuid "
        "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $f$ "
        f"BEGIN CASE p_table {branches} ELSE RETURN NULL; END CASE; END $f$",
        # The same rule as shared_scope.viewer_member_predicate.
        f"CREATE OR REPLACE FUNCTION {sch}.securo_shared_with_viewer() RETURNS SETOF uuid "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $f$ "
        f"SELECT s.transaction_id FROM {sch}.transaction_splits s "
        f"JOIN {sch}.group_members m ON m.id = s.group_member_id "
        f"JOIN {sch}.groups g ON g.id = m.group_id "
        f"WHERE m.linked_user_id = {viewer} "
        f"OR (m.is_self AND g.user_id = {viewer}) $f$",
        # How many bank connections the workspaces a user created hold, for the
        # quota (ADR 0049). A count and nothing else: returning rows here would
        # be a hole in the boundary.
        f"CREATE OR REPLACE FUNCTION {sch}.securo_owned_bank_connections(p_user uuid) RETURNS bigint "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $f$ "
        f"SELECT count(*) FROM {sch}.bank_connections c "
        f"JOIN {sch}.workspaces w ON w.id = c.workspace_id "
        "WHERE w.created_by_user_id = p_user AND NOT w.is_archived $f$",
    ]
    for signature in (
        "securo_row_workspace(text, uuid)",
        "securo_shared_with_viewer()",
        "securo_owned_bank_connections(uuid)",
    ):
        out.append(f"REVOKE ALL ON FUNCTION {sch}.{signature} FROM PUBLIC")
        out.append(f"GRANT EXECUTE ON FUNCTION {sch}.{signature} TO {rol}")
    for name in covered_tables(tables):
        table = f"{sch}.{quote(name)}"
        if name in VIA_PARENT:
            column, parent = VIA_PARENT[name]
            rule = f"{column} IN (SELECT id FROM {sch}.{quote(parent)} WHERE workspace_id = {current})"
        else:
            rule = f"workspace_id = {current}"
        out += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS securo_workspace ON {table}",
            f"CREATE POLICY securo_workspace ON {table} TO {rol} USING ({rule}) WITH CHECK ({rule})",
        ]
    # A member's share of an expense that lives in another workspace: read only.
    if "transactions" in tables:
        table = f"{sch}.transactions"
        out += [
            f"DROP POLICY IF EXISTS securo_shared ON {table}",
            f"CREATE POLICY securo_shared ON {table} FOR SELECT TO {rol} "
            f"USING (id IN (SELECT {sch}.securo_shared_with_viewer()))",
        ]
    return out


_ROLE_CHECK = text(
    "SELECT r.rolsuper OR r.rolbypassrls OR EXISTS ("
    " SELECT 1 FROM pg_class c WHERE c.relname = 'transactions' AND c.relkind = 'r'"
    " AND c.relnamespace = current_schema()::regnamespace"
    " AND pg_has_role(current_user, c.relowner, 'MEMBER')) AS bypasses,"
    " COALESCE((SELECT c.relrowsecurity FROM pg_class c WHERE c.relname = 'transactions'"
    " AND c.relkind = 'r' AND c.relnamespace = current_schema()::regnamespace), false) AS covered"
    " FROM pg_roles r WHERE r.rolname = current_user"
)


async def check_connection(session: AsyncSession) -> None:
    """Refuse to run believing in a boundary that isn't there."""
    if not enabled():
        return
    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("ROW_SECURITY_ENABLED needs PostgreSQL")
    bypasses, covered = (await session.execute(_ROLE_CHECK)).one()
    if bypasses:
        raise RuntimeError(
            "ROW_SECURITY_ENABLED is on, but DATABASE_URL connects as a role that bypasses row "
            "security (a superuser, BYPASSRLS, or the owner of the tables)."
        )
    if not covered:
        raise RuntimeError(
            "ROW_SECURITY_ENABLED is on, but the policies are not set up. Run "
            "`python -m app.cli apply-row-security <role>` as the owner of the tables."
        )
