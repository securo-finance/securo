"""What the operator of a managed instance reads about one account (ADR 0049).

The operator acts on accounts, never on what they hold: the answer carries
who the account is, the workspaces it created and its limits next to the
current counts, and nothing from inside a workspace. The counts come from
`quota_service.check_fit`, the same functions the limits are enforced with,
so this screen and a refusal can never disagree.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace
from app.services import quota_service


@dataclass(frozen=True)
class OwnedWorkspace:
    id: uuid.UUID
    name: str
    kind: str
    is_archived: bool
    members: int


@dataclass(frozen=True)
class CounterUsage:
    counter: str
    current: int
    maximum: Optional[int]


@dataclass(frozen=True)
class AccountView:
    user: User
    workspaces: list[OwnedWorkspace]
    usage: list[CounterUsage]


async def find_account(
    session: AsyncSession, *, user_id: Optional[uuid.UUID] = None, email: Optional[str] = None
) -> Optional[User]:
    if user_id is not None:
        return await session.get(User, user_id)
    if email is not None:
        query = select(User).where(func.lower(User.email) == email.strip().lower())
        return await session.scalar(query)
    return None


def password_is_set(user: User) -> bool:
    return bool(user.hashed_password)


async def describe(session: AsyncSession, user: User) -> AccountView:
    rows = await session.execute(
        select(Workspace.id, Workspace.name, Workspace.kind, Workspace.is_archived)
        .where(Workspace.created_by_user_id == user.id)
        .order_by(Workspace.created_at, Workspace.id)
    )
    workspaces = [
        OwnedWorkspace(r.id, r.name, r.kind, r.is_archived, await quota_service.count_members(session, r.id))
        for r in rows
    ]
    quota = await quota_service.get_quota(session, user.id)
    limits = {c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}
    fit = await quota_service.check_fit(session, user.id, limits)
    usage = [CounterUsage(f.counter, f.current, f.target) for f in fit.counters]
    return AccountView(user=user, workspaces=workspaces, usage=usage)
