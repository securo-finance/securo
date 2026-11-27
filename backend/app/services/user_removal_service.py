"""Removing a person from the instance without touching other people's data.

A row belongs to its workspace (ADR 0002), so a deletion takes the person's own
rows and the workspaces only they reach, and keeps what they wrote anywhere
else. When something kept still names them as its author, the users row stays
as a closed shell with nothing of the person in it (ADR 0047).
"""

import secrets
import uuid

import sqlalchemy as sa
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.group import GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

# Reserved by RFC 2606: no account can be registered or receive mail there.
_REMOVED_EMAIL = "removed-{id}@removed.invalid"
_REMOVED_EMAIL_PATTERN = _REMOVED_EMAIL.format(id="%")

# Rows that are the person's own wherever they sit: deleted with them.
PERSONAL_TABLES = (
    "access_tokens",
    "agent_conversations",
    "agent_llm_connections",
    "agent_llm_usage",
    "notification_preferences",
    "notifications",
    "ui_layouts",
    "user_passkeys",
    "user_quotas",
    "workspace_members",
)

# Rows of a workspace that name their author in a required column: kept, with
# the author's row kept behind them. Tables where the column is nullable need
# no entry, their foreign key clears it.
AUTHORED_TABLES = (
    "accounts",
    "agent_knowledge_docs",
    "agents",
    "asset_groups",
    "assets",
    "bank_connections",
    "budgets",
    "categories",
    "category_groups",
    "collections",
    "credit_card_bills",
    "document_extractions",
    "goals",
    "groups",
    "import_logs",
    "payee_mapping",
    "recurring_transactions",
    "rules",
    "transaction_attachments",
    "transactions",
)


class SoleOwnerError(ValueError):
    """The person is the only owner of a workspace that has other members."""

    def __init__(self, workspace_name: str):
        self.workspace_name = workspace_name
        super().__init__(
            f"{workspace_name} has other members and no other owner. "
            "Make one of them an owner, or remove them, before deleting this user"
        )


def not_removed()-> sa.ColumnElement[bool]:
    # An active account is never hidden, whatever its address says.
    return or_(User.is_active.is_(True), User.email.not_like(_REMOVED_EMAIL_PATTERN))


def _table(name: str) -> sa.TableClause:
    """The table by name: the agents models are only imported when that module is on."""
    loaded = Base.metadata.tables.get(name)
    if loaded is not None:
        return loaded
    return sa.table(name, sa.column("user_id", UUID(as_uuid=True)))


async def _workspaces_to_delete(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The workspaces this person owns and nobody else reaches.

    Locks every workspace they are in, so nobody becomes or stops being an
    owner until this commits. Refuses when one has members but no other owner: they would lose it, or be
    left with a workspace nobody can administer.
    """
    joined = select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
    workspaces = (
        await session.scalars(
            select(Workspace)
            .where(or_(Workspace.managed_by_user_id == user_id, Workspace.id.in_(joined)))
            .order_by(Workspace.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    doomed = []
    for workspace in workspaces:
        members = await session.execute(
            select(WorkspaceMember.user_id, WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace.id
            )
        )
        roles = {member_id: role for member_id, role in members}
        own_role = roles.pop(user_id, None)
        if own_role != "owner" and workspace.managed_by_user_id != user_id:
            continue
        if "owner" in roles.values() or workspace.managed_by_user_id not in (None, user_id):
            continue
        if roles:
            raise SoleOwnerError(workspace.name)
        doomed.append(workspace.id)
    return doomed


async def _delete_workspaces(session: AsyncSession, workspace_ids: list[uuid.UUID]) -> None:
    # What other workspaces hold on these ones and the cascade can't pass over.
    members = select(GroupMember.id).where(GroupMember.workspace_id.in_(workspace_ids))
    await session.execute(
        delete(TransactionSplit).where(TransactionSplit.group_member_id.in_(members))
    )
    await session.execute(
        delete(GroupSettlement).where(
            or_(
                GroupSettlement.from_member_id.in_(members),
                GroupSettlement.to_member_id.in_(members),
            )
        )
    )
    connections = select(BankConnection.id).where(BankConnection.workspace_id.in_(workspace_ids))
    await session.execute(
        update(Account)
        .where(Account.connection_id.in_(connections), Account.workspace_id.not_in(workspace_ids))
        .values(connection_id=None)
        .execution_options(synchronize_session=False)
    )
    # Every workspace_id cascades, so one statement takes the rest in any order.
    await session.execute(delete(Workspace).where(Workspace.id.in_(workspace_ids)))


async def _is_author(session: AsyncSession, user_id: uuid.UUID) -> bool:
    for name in AUTHORED_TABLES:
        table = _table(name)
        row = await session.execute(
            select(sa.literal(1)).select_from(table).where(table.c.user_id == user_id).limit(1)
        )
        if row.first() is not None:
            return True
    return False


def _close(user: User) -> None:
    """Strip the row to what the kept data reads: who wrote it, and in which currency."""
    currency = (user.preferences or {}).get("currency_display")
    # Every optional column, so one added later is stripped too.
    for column in User.__table__.columns:
        if column.nullable:
            setattr(user, column.key, None)
    user.email =_REMOVED_EMAIL.format(id=user.id)
    user.hashed_password = PasswordHelper().hash(secrets.token_urlsafe(32))
    user.is_active = False
    user.is_superuser = False
    user.is_verified = False
    user.preferences = {"currency_display": currency} if currency else {}
    user.is_2fa_enabled = False


async def remove_user(session: AsyncSession, user: User) -> None:
    """Delete the person; the caller has locked their row and checked that they may be deleted."""
    user_id = user.id
    workspace_ids = await _workspaces_to_delete(session, user_id)
    if workspace_ids:
        await _delete_workspaces(session, workspace_ids)
    for name in PERSONAL_TABLES:
        table = _table(name)
        await session.execute(delete(table).where(table.c.user_id == user_id))
    await session.execute(
        update(Workspace)
        .where(Workspace.managed_by_user_id == user_id)
        .values(managed_by_user_id=None)
    )
    if await _is_author(session, user_id):
        _close(user)
    else:
        # Core, not session.delete(): the relationships on User cascade to
        # rows that belong to workspaces.
        await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
