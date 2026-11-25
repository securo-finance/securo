"""Notifications inside the app (ADR 0041).

Most are conditions read from what Securo already stores: a
connection that stopped syncing, a bill that did not generate, a budget close
to its limit, a reminder or an invoice past its date, matches waiting for an
answer, a backup that failed. `_reconcile` makes a person's rows say
what is true now: it creates, refreshes, reopens and resolves them, so running
it again changes nothing and a missed run is made up by the next one.

The others only inform (`_EVENTS`): something another person did in a group,
told once by `_inform` where it happens.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, NamedTuple, Optional

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_timezone, app_today
from app.core.feature_flags import feature_flag
from app.models.bank_connection import BankConnection
from app.models.budget import Budget
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.notification import Notification, NotificationPreference
from app.models.plugin_connection import PluginConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.notification import NotificationKind, NotificationStatus
from app.services import notification_channels
from app.services.module_service import ModuleId, resolve_modules

logger = logging.getLogger(__name__)

CONNECTION_ERROR: NotificationKind = "connection_error"
RECURRING_FAILED: NotificationKind = "recurring_failed"
BUDGET_THRESHOLD: NotificationKind = "budget_threshold"
FLAG_OVERDUE: NotificationKind = "flag_overdue"
RECONCILIATION_PENDING: NotificationKind = "reconciliation_pending"
INVOICE_OVERDUE: NotificationKind = "invoice_overdue"
BACKUP_FAILED: NotificationKind = "backup_failed"
GROUP_EXPENSE: NotificationKind = "group_expense"
GROUP_SETTLEMENT: NotificationKind = "group_settlement"

#: Kinds that only inform: told once, where it happens, and done once read.
_EVENTS: tuple[NotificationKind, ...] = (GROUP_EXPENSE, GROUP_SETTLEMENT)


@dataclass(frozen=True)
class _Kind:
    #: The module it belongs to; a workspace without it never gets the kind.
    module: Optional[ModuleId] = None
    #: Whether only who can act on it hears of it, viewers left out.
    writers: bool = True


_KINDS: dict[NotificationKind, _Kind] = {
    CONNECTION_ERROR: _Kind(),
    BUDGET_THRESHOLD: _Kind(ModuleId.BUDGETS, writers=False),
    RECURRING_FAILED: _Kind(ModuleId.RECURRING),
    FLAG_OVERDUE: _Kind(ModuleId.TRANSACTIONS),
    RECONCILIATION_PENDING: _Kind(ModuleId.RULES),
    INVOICE_OVERDUE: _Kind(ModuleId.INVOICES),
    GROUP_EXPENSE: _Kind(ModuleId.SPLIT_GROUPS, writers=False),
    GROUP_SETTLEMENT: _Kind(ModuleId.SPLIT_GROUPS, writers=False),
    BACKUP_FAILED: _Kind(),
}

#: Failures only the person can end; the others may pass on their own, so they
#: are told only once a scheduled sync has failed this many times in a row.
_NEEDS_PERSON = frozenset({"consent_expired", "psu_present_required", "reauthorize_at_provider"})
_TRANSIENT_FAILURES = 3
#: A bill is late once its day and a whole day after it went by.
_RECURRING_GRACE = timedelta(days=2)
#: A condition that still holds (or is back) asks again after this long. Not
#: budgets: each threshold is told once a month.
_REMIND_AFTER = timedelta(days=7)
#: How long what is past stays on the page.
_KEEP_RESOLVED = timedelta(days=90)


@dataclass(frozen=True)
class _Wanted:
    user_id: uuid.UUID
    key: str
    data: dict[str, Any]
    #: For one that stands for many: the newest of them, sortable. A newer one
    #: asks again; fewer of what was already told does not.
    newest: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- Reading ----------------------------------------------------------------


def _shown(workspace_id: uuid.UUID, user_id: uuid.UUID) -> list[ColumnElement[bool]]:
    muted = select(NotificationPreference.kind).where(
        NotificationPreference.workspace_id == workspace_id,
        NotificationPreference.user_id == user_id,
        NotificationPreference.channel == notification_channels.IN_APP,
        NotificationPreference.enabled.is_(False),
    )
    return [
        Notification.workspace_id == workspace_id,
        Notification.user_id == user_id,
        Notification.kind.not_in(muted),
    ]


def _open(workspace_id: uuid.UUID, user_id: uuid.UUID) -> list[ColumnElement[bool]]:
    return [
        *_shown(workspace_id, user_id),
        Notification.resolved_at.is_(None),
        or_(Notification.kind.not_in(_EVENTS), Notification.read_at.is_(None)),
    ]


async def list_for_user(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: NotificationStatus = "open",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    """A page of a person's notifications, newest first, and how many there are."""
    where = _shown(workspace_id, user_id) if status == "all" else _open(workspace_id, user_id)
    if status == "unread":
        where.append(Notification.read_at.is_(None))
    total = await session.scalar(select(func.count()).select_from(Notification).where(*where)) or 0
    query = (
        select(Notification)
        .where(*where)
        .order_by(Notification.notified_at.desc(), Notification.id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(query)).scalars().all()), total


async def unread_count(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*_open(workspace_id, user_id), Notification.read_at.is_(None))
        )
    ) or 0


async def mark_read(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID
) -> bool:
    row = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
        )
    )
    if row is None:
        return False
    if row.read_at is None:
        row.read_at = _now()
        await session.commit()
    return True


async def mark_all_read(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await session.execute(
        update(Notification)
        .where(*_open(workspace_id, user_id), Notification.read_at.is_(None))
        .values(read_at=_now())
        .execution_options(synchronize_session=False)
    )
    await session.commit()


# --- Preferences ------------------------------------------------------------


def kinds_for(workspace: Workspace, *, can_write: bool) -> list[NotificationKind]:
    """The kinds someone with this access can get in this workspace."""
    modules = resolve_modules(workspace)
    return [
        kind
        for kind, spec in _KINDS.items()
        if (spec.module is None or spec.module.value in modules) and (can_write or not spec.writers)
    ]


# --- Asking for attention ---------------------------------------------------


async def _raise(session: AsyncSession, row: Notification, now: datetime) -> None:
    """The only place a notification starts asking for attention, new or again.

    Every channel hears of it from here and nowhere else."""
    row.notified_at = now
    row.read_at = None
    row.resolved_at = None
    session.add(row)
    await session.flush()
    await notification_channels.queue(session, [row])


# --- Reconciling ------------------------------------------------------------


async def _recipients(
    session: AsyncSession, workspace_id: uuid.UUID, kind: NotificationKind
) -> list[uuid.UUID]:
    """Active members and the external manager, those `kind` is for."""
    query = select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id)
    if _KINDS[kind].writers:
        query = query.where(WorkspaceMember.role.in_(("owner", "editor")))
    ids = set((await session.execute(query)).scalars().all())
    manager = await session.scalar(select(Workspace.managed_by_user_id).where(Workspace.id == workspace_id))
    if manager is not None:
        ids.add(manager)
    # Not a join: users.id is stored in another form on SQLite (see list_members).
    active = select(User).where(User.id.in_(ids), User.is_active.is_(True))
    ids = {user.id for user in (await session.execute(active)).unique().scalars()}
    return sorted(ids, key=str)


async def _reconcile(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    kind: NotificationKind,
    wanted: Iterable[_Wanted],
    *,
    key_prefix: Optional[str] = None,
) -> None:
    """Make the open notifications of `kind` exactly `wanted`. Does not commit.

    `key_prefix` narrows it to one subject (a single connection)."""
    query = select(Notification).where(Notification.workspace_id == workspace_id, Notification.kind == kind)
    if key_prefix is not None:
        query = query.where(Notification.dedup_key.startswith(key_prefix, autoescape=True))
    rows = {(row.user_id, row.dedup_key): row for row in (await session.execute(query)).scalars()}
    now = _now()
    for item in wanted:
        row = rows.pop((item.user_id, item.key), None)
        data, news = item.data, False
        if item.newest is not None:
            told = row.data.get("newest", "") if row is not None else ""
            # Back after everything was dealt with, it is news whatever its date.
            news = row is not None and (row.resolved_at is not None or item.newest > told)
            data = {**data, "newest": item.newest if news or row is None else told}
        if row is None:
            try:
                async with session.begin_nested():
                    row = Notification(
                        workspace_id=workspace_id,
                        user_id=item.user_id,
                        kind=kind,
                        dedup_key=item.key,
                        data=data,
                        created_at=now,
                    )
                    await _raise(session, row, now)
            except IntegrityError:
                # Another run told it first.
                logger.debug("Notification %s already exists", item.key)
            continue
        if row.data != data:
            row.data = data
        row.resolved_at = None
        if news or kind != BUDGET_THRESHOLD and now - _aware(row.notified_at) >= _REMIND_AFTER:
            await _raise(session, row, now)
    for row in rows.values():
        if row.resolved_at is None:
            row.resolved_at = now


def _has(workspace: Workspace, kind: str) -> bool:
    return kind in kinds_for(workspace, can_write=True)


# --- Connections ------------------------------------------------------------


def _connection_problem(connection: BankConnection) -> Optional[str]:
    from app.plugins.provider import PLUGIN_PROVIDER_PREFIX
    from app.services.connection_service import DISCONNECTED_STATUS, SYNC_BACKOFF_KEY

    # A plugin's bank connection mirrors its plugin connection, told as that.
    if connection.status == DISCONNECTED_STATUS or connection.provider.startswith(PLUGIN_PROVIDER_PREFIX):
        return None
    failure = connection.last_sync_failure
    code = failure["code"] if failure else None
    if code in _NEEDS_PERSON:
        return code
    if connection.status == "expired":
        return "consent_expired"
    if code is None and connection.status != "error":
        return None
    backoff = (connection.settings or {}).get(SYNC_BACKOFF_KEY)
    failures = backoff.get("failures") if isinstance(backoff, dict) else None
    if isinstance(failures, int) and failures >= _TRANSIENT_FAILURES:
        return code or "provider_error"
    return None


def _plugin_problem(connection: PluginConnection) -> Optional[str]:
    # Without an error, needs_auth is a first sign-in not finished yet.
    if connection.status == "needs_auth" and connection.last_error:
        return "consent_expired"
    if connection.status == "error" and (connection.failures or 0) >= _TRANSIENT_FAILURES:
        return "provider_error"
    return None


def _connection_prefix(connection: BankConnection | PluginConnection) -> str:
    return f"{CONNECTION_ERROR}:{connection.id}:"


def _connection_wanted(
    connection: BankConnection | PluginConnection, recipients: list[uuid.UUID]
) -> list[_Wanted]:
    data: dict[str, Any]
    if isinstance(connection, PluginConnection):
        code = _plugin_problem(connection)
        data = {"name": connection.name, "plugin_id": connection.plugin_id}
    else:
        code = _connection_problem(connection)
        data = {"name": connection.display_name or connection.institution_name}
    if code is None:
        return []
    data |= {"connection_id": str(connection.id), "code": code}
    return [_Wanted(user_id, f"{_connection_prefix(connection)}{code}", data) for user_id in recipients]


async def connection_changed(
    session: AsyncSession, connection: BankConnection | PluginConnection, *, removed: bool = False
) -> None:
    """Follow one connection in the caller's transaction, right after its state changed.

    Never fails the caller: the hourly evaluation reads the same state again."""
    # What the caller wrote fails here, as its own error, not swallowed below.
    await session.flush()
    try:
        async with session.begin_nested():
            wanted: list[_Wanted] = []
            if not removed and _connection_wanted(connection, [connection.workspace_id]):
                recipients = await _recipients(session, connection.workspace_id, CONNECTION_ERROR)
                wanted = _connection_wanted(connection, recipients)
            await _reconcile(
                session,
                connection.workspace_id,
                CONNECTION_ERROR,
                wanted,
                key_prefix=_connection_prefix(connection),
            )
    except Exception:
        logger.exception("Could not update the notifications of a connection")


async def _check_connections(session: AsyncSession, workspace: Workspace) -> None:
    recipients = await _recipients(session, workspace.id, CONNECTION_ERROR)
    found: list[BankConnection | PluginConnection] = list(
        (await session.execute(select(BankConnection).where(BankConnection.workspace_id == workspace.id))).scalars()
    )
    if feature_flag("PLUGINS_ENABLED"):
        found.extend(
            (
                await session.execute(select(PluginConnection).where(PluginConnection.workspace_id == workspace.id))
            ).scalars()
        )
    wanted = [item for connection in found for item in _connection_wanted(connection, recipients)]
    await _reconcile(session, workspace.id, CONNECTION_ERROR, wanted)


# --- Recurring --------------------------------------------------------------


async def _check_recurring(session: AsyncSession, workspace: Workspace) -> None:
    """Bills due days ago that the hourly generation left behind, whatever stopped it."""
    from app.services.recurring_transaction_service import adjust_weekend_date

    wanted: list[_Wanted] = []
    if _has(workspace, RECURRING_FAILED):
        late = app_today() - _RECURRING_GRACE
        bills = (
            await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.workspace_id == workspace.id,
                    RecurringTransaction.is_active.is_(True),
                    RecurringTransaction.auto_generate.is_(True),
                    RecurringTransaction.next_occurrence <= late,
                    or_(
                        RecurringTransaction.end_date.is_(None),
                        RecurringTransaction.next_occurrence <= RecurringTransaction.end_date,
                    ),
                    # One just added with a past start is generated within the hour.
                    RecurringTransaction.created_at <= _now() - timedelta(days=1),
                )
            )
        ).scalars().all()
        bills = [b for b in bills if adjust_weekend_date(b.next_occurrence, b.weekend_adjustment) <= late]
        if bills:
            recipients = await _recipients(session, workspace.id, RECURRING_FAILED)
            wanted = [
                _Wanted(
                    user_id,
                    f"{RECURRING_FAILED}:{bill.id}:{bill.next_occurrence.isoformat()}",
                    {
                        "recurring_id": str(bill.id),
                        "name": bill.description,
                        "date": bill.next_occurrence.isoformat(),
                    },
                )
                for bill in bills
                for user_id in recipients
            ]
    await _reconcile(session, workspace.id, RECURRING_FAILED, wanted)


# --- Budgets ----------------------------------------------------------------


def _threshold(actual: Decimal, limit: Optional[Decimal]) -> Optional[int]:
    """The threshold crossed by what was spent, never by what is still projected."""
    if not limit or limit <= 0:
        return None
    if actual >= limit:
        return 100
    return 80 if actual * 100 >= limit * 80 else None


async def _check_budgets(session: AsyncSession, workspace: Workspace) -> None:
    from app.services import budget_service
    from app.services.fx_rate_service import stored_rates_only

    month = app_today().replace(day=1)
    wanted: list[_Wanted] = []
    has_budget = _has(workspace, BUDGET_THRESHOLD) and await session.scalar(
        select(Budget.id)
        .where(
            Budget.workspace_id == workspace.id,
            or_(Budget.month == month, (Budget.month <= month) & Budget.is_recurring.is_(True)),
        )
        .limit(1)
    )
    if has_budget:
        # Read for each member: the amounts are in their display currency and
        # count only their share of what they split.
        for user_id in await _recipients(session, workspace.id, BUDGET_THRESHOLD):
            with stored_rates_only():
                rows = await budget_service.get_budget_vs_actual(session, workspace.id, user_id, month)
            user = await session.get(User, user_id)
            if user is None:
                continue
            for row in rows:
                threshold = _threshold(row.actual_amount, row.budget_amount)
                if threshold is None or row.budget_amount is None:
                    continue
                wanted.append(
                    _Wanted(
                        user_id,
                        f"{BUDGET_THRESHOLD}:{row.category_id}:{month.isoformat()}:{threshold}",
                        {
                            "category_id": str(row.category_id),
                            "name": row.category_name,
                            "month": month.isoformat(),
                            "threshold": threshold,
                            "actual": str(row.actual_amount.quantize(Decimal("0.01"))),
                            "budget": str(row.budget_amount.quantize(Decimal("0.01"))),
                            "currency": user.primary_currency,
                        },
                    )
                )
    await _reconcile(session, workspace.id, BUDGET_THRESHOLD, wanted)


# --- Reminders, matches and invoices ----------------------------------------


async def _tell(
    session: AsyncSession,
    workspace: Workspace,
    kind: NotificationKind,
    found: list[tuple[str, dict[str, Any]]],
    *,
    newest: Optional[str] = None,
) -> None:
    """The same (key, data) to everyone `kind` is for; nothing found resolves what was told."""
    recipients = await _recipients(session, workspace.id, kind) if found else []
    wanted = [
        _Wanted(user_id, f"{kind}:{key}", data, newest) for key, data in found for user_id in recipients
    ]
    await _reconcile(session, workspace.id, kind, wanted)


async def _tell_total(
    session: AsyncSession, workspace: Workspace, kind: NotificationKind, count: int, newest: Optional[date]
) -> None:
    """One notification for all there are, told again when a newer one arrives."""
    if newest is None:
        await _tell(session, workspace, kind, [])
    else:
        await _tell(session, workspace, kind, [("all", {"count": count})], newest=newest.isoformat())


async def _check_flags(session: AsyncSession, workspace: Workspace) -> None:
    """Told again when another reminder's date goes by."""
    from app.services import transaction_service

    count, latest = 0, None
    if _has(workspace, FLAG_OVERDUE):
        count, latest = await transaction_service.overdue_flags(session, workspace.id)
    await _tell_total(session, workspace, FLAG_OVERDUE, count, latest)


async def _check_reconciliation(session: AsyncSession, workspace: Workspace) -> None:
    """Told again on a day that brings new questions."""
    from app.services import reconciliation_suggestion_service

    count, day = 0, None
    if _has(workspace, RECONCILIATION_PENDING):
        count, newest = await reconciliation_suggestion_service.waiting(session, workspace.id)
        if newest is not None:
            day = _aware(newest).astimezone(app_timezone()).date()
    await _tell_total(session, workspace, RECONCILIATION_PENDING, count, day)


async def _check_invoices(session: AsyncSession, workspace: Workspace) -> None:
    from app.services import invoice_service

    found = []
    if _has(workspace, INVOICE_OVERDUE):
        for invoice, due in await invoice_service.overdue_invoices(session, workspace.id):
            if invoice.payee is not None:
                name = invoice.payee.name
            else:
                name = f"#{await invoice_service.display_number(session, invoice) or ''}"
            data = {
                "invoice_id": str(invoice.id),
                "name": name,
                "date": due.isoformat(),
                "amount": str(invoice_service.balance(invoice)),
                "currency": invoice.currency,
            }
            found.append((f"{invoice.id}:{due.isoformat()}", data))
    await _tell(session, workspace, INVOICE_OVERDUE, found)


# --- Groups -----------------------------------------------------------------


@dataclass
class _Told:
    """What one person is told of one thing that happened in a group."""

    user_id: uuid.UUID
    group: Group
    key: str
    data: dict[str, Any]


async def _inform(session: AsyncSession, kind: NotificationKind, told: list[_Told]) -> None:
    """Write events. One told before is told again only when it says something else."""
    now = _now()
    for item in told:
        user = await session.get(User, item.user_id)
        if user is None or not user.is_active:
            continue
        workspace = await _workspace_for(session, item.user_id, item.group.workspace_id)
        if workspace is None or not _has(workspace, kind):
            continue
        rows = {
            row.workspace_id: row
            for row in await session.scalars(
                select(Notification).where(
                    Notification.user_id == item.user_id, Notification.dedup_key == item.key
                )
            )
        }
        row = rows.pop(workspace.id, None)
        # Told elsewhere before they joined or left a workspace, that is the old news.
        for elsewhere in rows.values():
            if elsewhere.resolved_at is None:
                elsewhere.resolved_at = now
        if row is None:
            row = Notification(
                workspace_id=workspace.id,
                user_id=item.user_id,
                kind=kind,
                dedup_key=item.key,
                data=item.data,
                created_at=now,
            )
        elif _says(row.data) == _says(item.data) and row.resolved_at is None:
            row.data = item.data
            continue
        row.data = item.data
        await _raise(session, row, now)


def _says(data: dict[str, Any]) -> dict[str, Any]:
    """What a person reads of it: a payment saved again gets a new id and says the same."""
    return {key: value for key, value in data.items() if key != "settlement_id"}


async def _workspace_for(
    session: AsyncSession, user_id: uuid.UUID, group_workspace_id: uuid.UUID
) -> Optional[Workspace]:
    """Where a person hears of a group: its workspace when they are in it,
    else their own first one (a group shows in every workspace of theirs)."""
    from app.services import workspace_service

    workspace = await session.get(Workspace, group_workspace_id)
    if workspace is not None and not workspace.is_archived:
        if workspace.managed_by_user_id == user_id:
            return workspace
        if await workspace_service.get_membership(session, workspace.id, user_id) is not None:
            return workspace
    return await workspace_service.get_default_workspace(session, user_id)


class _Member(NamedTuple):
    """A member as stored. Read by column on purpose: in a request that listed
    the group, `GroupMember.is_self` holds who the *viewer* is instead."""

    id: uuid.UUID
    name: str
    linked_user_id: Optional[uuid.UUID]
    is_self: bool

    def person(self, group: Group) -> Optional[uuid.UUID]:
        """The account behind it; the owner's own entry is often not linked."""
        return self.linked_user_id or (group.user_id if self.is_self else None)


async def _members(session: AsyncSession, group_id: uuid.UUID) -> dict[uuid.UUID, _Member]:
    rows = await session.execute(
        select(GroupMember.id, GroupMember.name, GroupMember.linked_user_id, GroupMember.is_self).where(
            GroupMember.group_id == group_id
        )
    )
    return {row.id: _Member(*row) for row in rows}


async def _actor_name(
    session: AsyncSession, actor_id: uuid.UUID, group: Group, members: dict[uuid.UUID, _Member]
) -> str:
    """Never their email: it is read by people of other workspaces."""
    for member in members.values():
        if member.person(group) == actor_id:
            return member.name
    user = await session.get(User, actor_id)
    return (user.display_name if user is not None else None) or group.name


class _Share(NamedTuple):
    transaction_id: uuid.UUID
    person: uuid.UUID
    group: Group
    amount: Decimal


async def _shares(session: AsyncSession, transaction_ids: list[uuid.UUID]) -> list[_Share]:
    """Each split of these expenses that belongs to someone with an account."""
    rows = await session.execute(
        select(
            TransactionSplit.transaction_id,
            TransactionSplit.share_amount,
            GroupMember.id,
            GroupMember.name,
            GroupMember.linked_user_id,
            GroupMember.is_self,
            Group,
        )
        .join(GroupMember, GroupMember.id == TransactionSplit.group_member_id)
        .join(Group, Group.id == GroupMember.group_id)
        .where(TransactionSplit.transaction_id.in_(transaction_ids))
    )
    shares = []
    for transaction_id, amount, *member, group in rows:
        person = _Member(*member).person(group)
        if person is not None:
            shares.append(_Share(transaction_id, person, group, amount))
    return shares


async def shares_of(session: AsyncSession, transaction_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Each person's share of one expense, to tell later what an edit changed."""
    totals: dict[uuid.UUID, Decimal] = {}
    for share in await _shares(session, [transaction_id]):
        totals[share.person] = totals.get(share.person, Decimal(0)) + share.amount
    return totals


async def expenses_shared(
    session: AsyncSession,
    actor_id: uuid.UUID,
    transactions: list[Transaction],
    *,
    before: Optional[dict[uuid.UUID, Decimal]] = None,
) -> None:
    """Tell whoever shares these expenses that `actor_id` split them.

    Several at once (installments, a batch) are one notification per person.
    `before` is each one's share before an edit: only those it changed are told.
    Runs in the caller's transaction and never fails it."""
    await session.flush()
    try:
        async with session.begin_nested():
            await _tell_expenses(session, actor_id, transactions, before)
    except Exception:
        logger.exception("Could not tell of a shared expense")


async def _tell_expenses(
    session: AsyncSession,
    actor_id: uuid.UUID,
    transactions: list[Transaction],
    before: Optional[dict[uuid.UUID, Decimal]],
) -> None:
    by_id = {tx.id: tx for tx in transactions}
    if not by_id:
        return
    shares: dict[tuple[uuid.UUID, uuid.UUID], dict[uuid.UUID, Decimal]] = {}
    groups: dict[uuid.UUID, Group] = {}
    for share in await _shares(session, list(by_id)):
        # Whoever did it knows, and so does the expense's own author.
        if share.person in (actor_id, by_id[share.transaction_id].user_id) or share.amount <= 0:
            continue
        groups[share.group.id] = share.group
        mine = shares.setdefault((share.person, share.group.id), {})
        mine[share.transaction_id] = mine.get(share.transaction_id, Decimal(0)) + share.amount
    told = []
    for (person, group_id), mine in shares.items():
        group = groups[group_id]
        data: dict[str, Any] = {
            "group_id": str(group.id),
            "group": group.name,
            "actor": await _actor_name(session, actor_id, group, await _members(session, group.id)),
        }
        if len(mine) > 1:
            first = next(tx.id for tx in transactions if tx.id in mine)
            ids = [str(tx.id) for tx in transactions if tx.id in mine]
            data |= {"count": len(mine), "transaction_ids": ids}
            told.append(_Told(person, group, f"{GROUP_EXPENSE}:batch:{first}", data))
            continue
        [(transaction_id, amount)] = mine.items()
        if before is not None and before.get(person) == amount:
            continue
        tx = by_id[transaction_id]
        data |= {
            "transaction_id": str(tx.id),
            "name": tx.description,
            "share": str(amount),
            "currency": tx.currency,
            "changed": before is not None and person in before,
        }
        told.append(_Told(person, group, f"{GROUP_EXPENSE}:{tx.id}", data))
    await _inform(session, GROUP_EXPENSE, told)


async def settlement_recorded(
    session: AsyncSession, actor_id: uuid.UUID, settlement: GroupSettlement, *, edited: bool = False
) -> None:
    """Tell the other side of a payment between members. Never fails the caller.

    Edited, it is told again as it is now, and what it said to anyone else ends."""
    await session.flush()
    try:
        async with session.begin_nested():
            group = await session.get(Group, settlement.group_id)
            if group is None:
                return
            members = await _members(session, group.id)
            payer, receiver = members[settlement.from_member_id], members[settlement.to_member_id]
            # Recorded again for the same incoming transaction (its links are
            # rewritten whole), it is still the same payment.
            subject = settlement.receiver_transaction_id or settlement.id
            data = {
                "settlement_id": str(settlement.id),
                "group_id": str(group.id),
                "group": group.name,
                "actor": await _actor_name(session, actor_id, group, members),
                "payer": payer.name,
                "receiver": receiver.name,
                "amount": str(Decimal(settlement.amount).quantize(Decimal("0.01"))),
                "currency": settlement.currency,
            }
            people = {payer.person(group), receiver.person(group)} - {None, actor_id}
            key = f"{GROUP_SETTLEMENT}:{subject}:{payer.id}"
            if edited:
                # Each person's notice is in a workspace of theirs, so it is found by the payment.
                rows = await session.scalars(
                    select(Notification).where(
                        Notification.kind == GROUP_SETTLEMENT,
                        Notification.resolved_at.is_(None),
                        Notification.data["settlement_id"].as_string() == str(settlement.id),
                    )
                )
                for row in rows:
                    if row.user_id not in people or row.dedup_key != key:
                        row.resolved_at = _now()
            await _inform(
                session, GROUP_SETTLEMENT, [_Told(p, group, key, data) for p in people if p is not None]
            )
    except Exception:
        logger.exception("Could not tell of a group payment")


async def _check_group_events(session: AsyncSession, workspace: Workspace) -> None:
    """What was told of something undone since has nothing left to say."""
    rows = (
        await session.execute(
            select(Notification).where(
                Notification.workspace_id == workspace.id,
                Notification.kind.in_(_EVENTS),
                Notification.resolved_at.is_(None),
            )
        )
    ).scalars().all()
    for row in rows:
        if not _has(workspace, row.kind):
            there = False
        elif row.kind == GROUP_SETTLEMENT:
            there = await session.get(GroupSettlement, uuid.UUID(row.data["settlement_id"])) is not None
        else:
            # One expense, or a batch: there while any of it is still shared with them in that group.
            ids = row.data.get("transaction_ids") or [row.data["transaction_id"]]
            there = any(
                share.person == row.user_id and share.amount > 0 and str(share.group.id) == row.data["group_id"]
                for share in await _shares(session, [uuid.UUID(value) for value in ids])
            )
        if not there:
            row.resolved_at = _now()


# --- Backups ----------------------------------------------------------------


async def _check_backups(session: AsyncSession, workspace: Workspace) -> None:
    from app.services import backup_service

    config = await backup_service.get_backup_config(session, workspace.id)
    wanted: list[_Wanted] = []
    if config.scheduled_enabled and config.storage_shared and config.last_failure_at is not None:
        data = {"failed_at": config.last_failure_at.isoformat()}
        wanted = [
            _Wanted(user_id, BACKUP_FAILED, data)
            for user_id in await _recipients(session, workspace.id, BACKUP_FAILED)
        ]
    await _reconcile(session, workspace.id, BACKUP_FAILED, wanted)


# --- Scheduled --------------------------------------------------------------

_CHECKS = (
    _check_connections,
    _check_recurring,
    _check_budgets,
    _check_flags,
    _check_reconciliation,
    _check_invoices,
    _check_group_events,
    _check_backups,
)


async def evaluate_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Every check, each on its own: one that fails leaves its notifications as they were."""
    for check in _CHECKS:
        try:
            workspace = await session.get(Workspace, workspace_id)
            if workspace is None or workspace.is_archived:
                return
            await check(session, workspace)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Notification check %s failed for workspace %s", check.__name__, workspace_id)


async def prune(session: AsyncSession) -> None:
    await session.execute(
        delete(Notification)
        .where(
            or_(
                Notification.resolved_at < _now() - _KEEP_RESOLVED,
                Notification.kind.in_(_EVENTS) & (Notification.notified_at < _now() - _KEEP_RESOLVED),
            )
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
