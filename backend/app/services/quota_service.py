"""Per-account usage limits on a managed instance (ADR 0049).

Neutral on purpose: four numbers per user, written by the operator, and the
code attaches nothing else to them. Everything is counted from the rows the
user owns (the workspaces they created and what those hold), never from
stored counters, so deleting a connection or archiving a workspace gives the
room back at once. With MANAGED_INSTANCE off nothing here reads the table and
every check passes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import row_security
from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.user_quota import UserQuota
from app.models.workspace import Workspace, WorkspaceMember

PERSONAL_WORKSPACES = "max_personal_workspaces"
BUSINESS_WORKSPACES = "max_business_workspaces"
BANK_CONNECTIONS = "max_bank_connections"
MEMBERS_PER_WORKSPACE = "max_members_per_workspace"
COUNTERS = (PERSONAL_WORKSPACES, BUSINESS_WORKSPACES, BANK_CONNECTIONS, MEMBERS_PER_WORKSPACE)

_WORKSPACE_COUNTER = {"personal": PERSONAL_WORKSPACES, "business": BUSINESS_WORKSPACES}


def enforced() -> bool:
    return get_settings().managed_instance


def _owned_by(user_id: uuid.UUID):
    return (Workspace.created_by_user_id == user_id) & Workspace.is_archived.is_(False)


async def get_quota(session: AsyncSession, user_id: uuid.UUID) -> Optional[UserQuota]:
    return await session.get(UserQuota, user_id)


async def count_workspaces(session: AsyncSession, user_id: uuid.UUID, kind: str) -> int:
    query = select(func.count()).select_from(Workspace).where(_owned_by(user_id), Workspace.kind == kind)
    return int(await session.scalar(query) or 0)


async def count_bank_connections(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Connections across every workspace the user created.

    `bank_connections` is behind row security, which would show only the
    workspace this request is bound to; the definer function answers the count
    across all of them, and only the count.
    """
    if row_security.enabled() and session.get_bind().dialect.name == "postgresql":
        query = text("SELECT securo_owned_bank_connections(:user)")
        return int(await session.scalar(query, {"user": user_id}) or 0)
    query = (
        select(func.count())
        .select_from(BankConnection)
        .join(Workspace, Workspace.id == BankConnection.workspace_id)
        .where(_owned_by(user_id))
    )
    return int(await session.scalar(query) or 0)


def _seats():
    """Members of a workspace other than the person who created it."""
    return (
        select(WorkspaceMember.workspace_id, func.count().label("seats"))
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id.is_distinct_from(Workspace.created_by_user_id))
        .group_by(WorkspaceMember.workspace_id)
    )


async def count_members(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    query = _seats().where(WorkspaceMember.workspace_id == workspace_id)
    row = (await session.execute(query)).first()
    return int(row.seats) if row else 0


async def _members_by_workspace(session: AsyncSession, user_id: uuid.UUID) -> list[int]:
    rows = await session.execute(_seats().where(_owned_by(user_id)))
    return [int(r.seats) for r in rows]


def _refuse(counter: str, current: int, maximum: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "quota_exceeded",
            "counter": counter,
            "current": current,
            "maximum": maximum,
            "message": "This account has reached its limit for this resource.",
        },
    )


async def _check(session: AsyncSession, user_id: Optional[uuid.UUID], counter: str, current_of) -> None:
    if not enforced() or user_id is None:
        return
    quota = await get_quota(session, user_id)
    maximum = getattr(quota, counter) if quota is not None else None
    if maximum is None:
        return
    current = await current_of()
    if current >= maximum:
        raise _refuse(counter, current, maximum)


async def check_new_workspace(session: AsyncSession, user_id: uuid.UUID, kind: str) -> None:
    counter = _WORKSPACE_COUNTER.get(kind)
    if counter is None:
        return
    await _check(session, user_id, counter, lambda: count_workspaces(session, user_id, kind))


async def _workspace_owner(session: AsyncSession, workspace_id: uuid.UUID) -> Optional[uuid.UUID]:
    return await session.scalar(select(Workspace.created_by_user_id).where(Workspace.id == workspace_id))


async def check_new_bank_connection(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """A connection fills the number of whoever created the workspace it goes into."""
    if not enforced():
        return
    owner = await _workspace_owner(session, workspace_id)
    if owner is None:
        return
    await _check(session, owner, BANK_CONNECTIONS, lambda: count_bank_connections(session, owner))


async def check_new_member(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if not enforced():
        return
    owner = await _workspace_owner(session, workspace_id)
    if owner is None or owner == user_id:
        return
    await _check(session, owner, MEMBERS_PER_WORKSPACE, lambda: count_members(session, workspace_id))


@dataclass(frozen=True)
class CounterFit:
    counter: str
    current: int
    target: Optional[int]
    excess: int


@dataclass(frozen=True)
class FitCheck:
    allowed: bool
    counters: list[CounterFit]


async def check_fit(
    session: AsyncSession, user_id: uuid.UUID, target: Mapping[str, Optional[int]]
) -> FitCheck:
    """Whether the account already fits `target`, per counter.

    Nothing is deleted: the person removes the excess and asks again. For the
    member limit, `current` is the fullest workspace and `excess` the seats
    over the target summed across workspaces.
    """
    members = await _members_by_workspace(session, user_id)
    current = {
        PERSONAL_WORKSPACES: await count_workspaces(session, user_id, "personal"),
        BUSINESS_WORKSPACES: await count_workspaces(session, user_id, "business"),
        BANK_CONNECTIONS: await count_bank_connections(session, user_id),
        MEMBERS_PER_WORKSPACE: max(members, default=0),
    }
    fits = []
    for counter in COUNTERS:
        limit = target.get(counter)
        if limit is None:
            excess = 0
        elif counter == MEMBERS_PER_WORKSPACE:
            excess = sum(max(0, seats - limit) for seats in members)
        else:
            excess = max(0, current[counter] - limit)
        fits.append(CounterFit(counter, current[counter], limit, excess))
    return FitCheck(allowed=all(f.excess == 0 for f in fits), counters=fits)


async def set_quota(session: AsyncSession, user_id: uuid.UUID, values: Mapping[str, Optional[int]]) -> UserQuota:
    quota = await get_quota(session, user_id)
    if quota is None:
        quota = UserQuota(user_id=user_id)
        session.add(quota)
    for counter in COUNTERS:
        setattr(quota, counter, values.get(counter))
    await session.flush()
    return quota
