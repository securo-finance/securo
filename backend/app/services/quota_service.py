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
from datetime import datetime, timezone
from typing import Mapping, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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


async def _lock(session: AsyncSession, key: str) -> None:
    """One quota count-and-insert per key at a time, until the transaction ends.

    The count and the insert that follows it run in the caller's transaction,
    so under READ COMMITTED two parallel requests from the same owner could
    both count below the limit and both insert, going over it by one per
    request. The transaction-scoped advisory lock makes the second request wait
    for the first to commit and then count its row. PostgreSQL only, in the
    same shape as `_lock_day` in `fx_rate_service.py`: SQLite, which the tests
    use, serialises writers itself.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(key)))
    )


async def _check(
    session: AsyncSession,
    user_id: Optional[uuid.UUID],
    counter: str,
    current_of,
    lock_key: str,
) -> None:
    if not enforced() or user_id is None:
        return
    quota = await get_quota(session, user_id)
    maximum = getattr(quota, counter) if quota is not None else None
    if maximum is None:
        return
    await _lock(session, lock_key)
    current = await current_of()
    if current >= maximum:
        raise _refuse(counter, current, maximum)


async def check_new_workspace(session: AsyncSession, user_id: uuid.UUID, kind: str) -> None:
    counter = _WORKSPACE_COUNTER.get(kind)
    if counter is None:
        return
    await _check(
        session,
        user_id,
        counter,
        lambda: count_workspaces(session, user_id, kind),
        f"quota:workspaces:{user_id}",
    )


async def _workspace_owner(session: AsyncSession, workspace_id: uuid.UUID) -> Optional[uuid.UUID]:
    return await session.scalar(select(Workspace.created_by_user_id).where(Workspace.id == workspace_id))


async def check_new_bank_connection(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """A connection fills the number of whoever created the workspace it goes into."""
    if not enforced():
        return
    owner = await _workspace_owner(session, workspace_id)
    if owner is None:
        return
    await _check(
        session,
        owner,
        BANK_CONNECTIONS,
        lambda: count_bank_connections(session, owner),
        f"quota:connections:{owner}",
    )


async def check_new_member(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: Optional[uuid.UUID]
) -> None:
    """`user_id` None: a person with no account yet, so never the creator."""
    if not enforced():
        return
    owner = await _workspace_owner(session, workspace_id)
    if owner is None or owner == user_id:
        return
    await _check(
        session,
        owner,
        MEMBERS_PER_WORKSPACE,
        lambda: count_members(session, workspace_id),
        f"quota:members:{workspace_id}",
    )


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
    """Write the account's four numbers in one upsert.

    Reading the row and creating it when missing let two first writes at once
    both read nothing and both insert, and the second failed on the primary
    key (issue #1983). The upsert makes the second wait for the first and then
    update the row.
    """
    row: dict[str, object] = {counter: values.get(counter) for counter in COUNTERS}
    row["updated_at"] = datetime.now(timezone.utc)
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(UserQuota).values(user_id=user_id, **row)
    await session.execute(statement.on_conflict_do_update(index_elements=[UserQuota.user_id], set_=row))
    quota = await session.get(UserQuota, user_id, populate_existing=True)
    assert quota is not None
    return quota


# --- An excess that stays (ADR 0055) -----------------------------------------


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def in_effect(quota: Optional[UserQuota], now: Optional[datetime] = None) -> bool:
    """Whether the operator's date for this account has come."""
    if quota is None or quota.excess_effective_at is None:
        return False
    return _aware(quota.excess_effective_at) <= (now or datetime.now(timezone.utc))


class SyncPausedError(Exception):
    """Sync was asked of a workspace whose creator holds more connections than their number."""

    def __init__(self, current: int, maximum: int) -> None:
        super().__init__("Sync is paused while this account is above its bank connection limit.")
        self.current = current
        self.maximum = maximum

    def detail(self) -> dict:
        return {
            "code": "quota_sync_paused",
            "counter": BANK_CONNECTIONS,
            "current": self.current,
            "maximum": self.maximum,
            "message": str(self),
        }


async def check_sync(
    session: AsyncSession, workspace_id: uuid.UUID, now: Optional[datetime] = None
) -> None:
    """Refuse a sync while the workspace's creator is above the connection number past the date.

    Derived from the rows every time, so removing a connection ends the pause
    with no one acting. Nothing is disconnected or deleted.
    """
    if not enforced():
        return
    owner = await _workspace_owner(session, workspace_id)
    if owner is None:
        return
    quota = await get_quota(session, owner)
    if quota is None or quota.max_bank_connections is None or not in_effect(quota, now):
        return
    current = await count_bank_connections(session, owner)
    if current > quota.max_bank_connections:
        raise SyncPausedError(current, quota.max_bank_connections)


async def set_excess_date(
    session: AsyncSession, user_id: uuid.UUID, moment: Optional[datetime]
) -> UserQuota:
    """Write the date after which an excess has an effect (None: never), in one upsert.

    Leaves the four numbers as they are. An account with no row gets one with
    every number unbounded, so the date alone changes nothing until a number is set.
    """
    row: dict[str, object] = {
        "excess_effective_at": _aware(moment) if moment is not None else None,
        "updated_at": datetime.now(timezone.utc),
    }
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(UserQuota).values(user_id=user_id, **row)
    await session.execute(statement.on_conflict_do_update(index_elements=[UserQuota.user_id], set_=row))
    quota = await session.get(UserQuota, user_id, populate_existing=True)
    assert quota is not None
    return quota


@dataclass(frozen=True)
class Standing:
    """Where an account stands against its own numbers, as the person sees it."""

    effective_at: Optional[datetime]
    in_effect: bool
    sync_paused: bool
    fit: FitCheck


async def standing(
    session: AsyncSession, user_id: uuid.UUID, now: Optional[datetime] = None
) -> Optional[Standing]:
    """None when the account has no numbers: nothing to be above."""
    quota = await get_quota(session, user_id)
    if quota is None:
        return None
    fit = await check_fit(session, user_id, {c: getattr(quota, c) for c in COUNTERS})
    effective = in_effect(quota, now)
    connections = next(f for f in fit.counters if f.counter == BANK_CONNECTIONS)
    return Standing(
        effective_at=_aware(quota.excess_effective_at) if quota.excess_effective_at else None,
        in_effect=effective,
        sync_paused=effective and connections.excess > 0,
        fit=fit,
    )
