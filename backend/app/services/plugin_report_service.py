"""Reports a plugin declares: a query Securo runs and a chart Securo draws (ADR 0028).

Nothing of the plugin runs. The report names what to add up (`measure`), how
to split it (`group_by`) and, optionally, which categories count; this module
adds up the workspace's transactions with the same rules as Securo's own
spending figures (`_query_filters`): posted rows, category lines, no
transfers or ignored categories, in the viewer's primary currency, by the
reporting date the card accounting mode picks.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins.manifest import Manifest, Report, parse_manifest
from app.services._query_filters import (
    _others_share_in_primary,
    _viewer_member_ids,
    any_line_in,
    counts_as_user_pnl,
    line_category_id,
    line_primary_or_amount,
    reporting_date_col,
    with_category_lines,
)
from app.services.rule_service import resolve_default_category_keys
from app.services.admin_service import get_credit_card_accounting_mode

#: The key a group's row takes when it has none (no category, no payee).
NONE_KEY = "none"
#: The key the groups past the report's `limit` add up under.
OTHER_KEY = "other"
#: Longest period a report reads, as Securo's own reports.
MAX_DAYS = 731


class PluginReportNotFound(Exception):
    """The plugin isn't installed in the workspace, or has no report by that key."""


@dataclass(frozen=True)
class ReportRow:
    key: str
    #: What the row is called; None for the "none" and "other" rows, which the frontend names.
    label: Optional[str]
    value: Decimal
    color: Optional[str] = None


@dataclass(frozen=True)
class ReportResult:
    report: Report
    currency: str
    rows: list[ReportRow]
    total: Decimal


async def installed_report(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, key: str
) -> tuple[WorkspacePlugin, Manifest, Report]:
    """The report as the workspace installed it (never from the catalog)."""
    record = await session.scalar(
        select(WorkspacePlugin).where(
            WorkspacePlugin.workspace_id == workspace_id, WorkspacePlugin.plugin_id == plugin_id
        )
    )
    if record is None or not record.manifest:
        raise PluginReportNotFound
    manifest = parse_manifest(record.manifest)
    report = next((r for r in manifest.contributes.reports if r.key == key), None)
    if report is None:
        raise PluginReportNotFound
    return record, manifest, report


async def _category_ids(
    session: AsyncSession, workspace_id: uuid.UUID, manifest: Manifest, keys: list[str]
) -> set[uuid.UUID]:
    """The workspace's categories a report's keys name.

    A key the plugin contributes is the category it created (or the one of
    the same name it found there on install); any other key is one of
    Securo's defaults. A key that names nothing in the workspace is skipped.
    """
    rows = (
        await session.execute(
            select(Category.id, Category.name, Category.source, Category.source_key).where(
                Category.workspace_id == workspace_id
            )
        )
    ).all()
    own = {source_key: cid for cid, _, source, source_key in rows if source == manifest.id and source_key}
    by_name = {name: cid for cid, name, _, _ in rows}
    defaults = {k: uuid.UUID(v) for k, v in resolve_default_category_keys({n: str(c) for n, c in by_name.items()}).items()}
    contributed = {c.key: c for c in manifest.contributes.categories}
    found: set[uuid.UUID] = set()
    for key in keys:
        if key in contributed:
            match = own.get(key) or next(
                (by_name[name] for name in contributed[key].name.values() if name in by_name), None
            )
        else:
            match = defaults.get(key)
        if match is not None:
            found.add(match)
    return found


def _month(day: date) -> str:
    return day.strftime("%Y-%m")


def _months_between(start: date, end: date) -> list[str]:
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


async def _owner_share_offsets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    report: Report,
    start: date,
    end: date,
    report_date: Any,
    currency: str,
    account_ids: Optional[list[uuid.UUID]],
    category_ids: Optional[set[uuid.UUID]],
) -> dict[str, tuple[Decimal, Decimal]]:
    """The non-owner shares of the viewer's own split rows, per report row.

    A split expense the viewer paid counts for the viewer's share only, the
    share-only model the dashboard and the native reports keep: this reads
    what the other members owe on the viewer's own split rows, by the same
    group key and filters the report totals use, so `run_report` takes them
    off each row. Empty for a workspace without group splits.
    """
    from app.models.transaction_split import TransactionSplit

    owner_columns = (
        Transaction.id,
        Transaction.category_id,
        Transaction.type,
        Transaction.currency,
        Transaction.amount,
        Transaction.amount_primary,
        Transaction.date,
    )
    stmt = (
        select(*owner_columns, func.sum(TransactionSplit.share_amount))
        .select_from(TransactionSplit)
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
    )
    if report.group_by == "category":
        key_col: Any = Category.id
        stmt = stmt.outerjoin(Category, Transaction.category_id == Category.id)
    elif report.group_by == "category_group":
        key_col = CategoryGroup.id
        stmt = stmt.outerjoin(Category, Transaction.category_id == Category.id).outerjoin(
            CategoryGroup, Category.group_id == CategoryGroup.id
        )
    elif report.group_by == "payee":
        key_col = func.coalesce(Payee.name, Transaction.payee)
        stmt = stmt.outerjoin(Payee, Transaction.payee_id == Payee.id)
    elif report.group_by == "account":
        key_col = Account.id
        stmt = stmt.join(Account, Transaction.account_id == Account.id)
    else:  # month
        key_col = report_date

    filters: list[Any] = [
        Transaction.user_id == user_id,
        Transaction.workspace_id == workspace_id,
        TransactionSplit.group_member_id.notin_(_viewer_member_ids(user_id)),
        report_date >= start,
        report_date <= end,
        Transaction.source != "opening_balance",
        Transaction.status == "posted",
        counts_as_user_pnl(Transaction.category_id),
    ]
    if account_ids is not None:
        filters.append(Transaction.account_id.in_(account_ids))
    if category_ids is not None:
        filters.append(or_(Transaction.category_id.in_(category_ids), any_line_in(category_ids)))

    result = await session.execute(
        stmt.add_columns(key_col).where(*filters).group_by(*owner_columns, key_col)
    )
    offsets: dict[str, tuple[Decimal, Decimal]] = {}
    rates: dict[tuple[str, date], Optional[Decimal]] = {}
    for row in result.all():
        tx_id, category_id, tx_type, currency_code, amount, amount_primary, tx_date = row[:7]
        others, group = row[7], row[8]
        # A share comes off at the rate stored on its row, the same value
        # the total it is subtracted from was built with, never today's.
        share = await _others_share_in_primary(
            session, (tx_id, category_id, tx_type, currency_code, amount, amount_primary, tx_date, others),
            currency, rates,
        )
        if not share:
            continue
        key = _month(group) if report.group_by == "month" else (
            str(group) if group is not None else NONE_KEY
        )
        credit, debit = offsets.get(key, (Decimal("0"), Decimal("0")))
        if tx_type == "credit":
            credit += share
        else:
            debit += share
        offsets[key] = (credit, debit)
    return offsets


def _non_owner_offset(measure: str, credit: Decimal, debit: Decimal) -> Decimal:
    """The part of a row's total that belongs to the other members."""
    if measure == "expenses":
        return debit
    if measure == "income":
        return credit
    return credit - debit


async def run_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    manifest: Manifest,
    report: Report,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> ReportResult:
    """Add up the period's transactions as the report asks."""
    if end < start or (end - start).days > MAX_DAYS:
        raise ValueError(f"the period must run forward and span at most {MAX_DAYS} days")
    user = await session.get(User, user_id)
    currency = user.primary_currency if user else get_settings().default_currency
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)
    end = min(end, app_today())

    amount = line_primary_or_amount()
    if report.measure == "expenses":
        types: tuple[str, ...] = ("debit",)
        value = func.abs(amount)
    elif report.measure == "income":
        types = ("credit",)
        value = func.abs(amount)
    else:
        types = ("debit", "credit")
        value = case((Transaction.type == "credit", func.abs(amount)), else_=-func.abs(amount))

    scope: list[Any] = []
    category_ids: Optional[set[uuid.UUID]] = None
    if account_ids is not None:
        scope.append(Transaction.account_id.in_(account_ids))
    if report.categories:
        category_ids = await _category_ids(session, workspace_id, manifest, report.categories)
        if not category_ids:
            return ReportResult(report, currency, [], Decimal("0"))
        scope.append(line_category_id().in_(category_ids))
    filters: list[Any] = [
        Transaction.workspace_id == workspace_id,
        Transaction.type.in_(types),
        report_date >= start,
        report_date <= end,
        Transaction.source != "opening_balance",
        Transaction.status == "posted",
        counts_as_user_pnl(line_category_id()),
        *scope,
    ]
    offsets = await _owner_share_offsets(
        session, workspace_id, user_id, report, start, end, report_date, currency, account_ids, category_ids
    )

    if report.group_by == "month":
        stmt = with_category_lines(select(report_date, func.sum(value)).select_from(Transaction))
        result = await session.execute(stmt.where(*filters).group_by(report_date))
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for day, total in result.all():
            if day is not None:
                totals[_month(day)] += Decimal(str(total or 0))
        for month, (credit, debit) in offsets.items():
            adjusted = totals.get(month, Decimal("0")) - _non_owner_offset(report.measure, credit, debit)
            # As the dashboard's period totals: a month never goes below zero.
            totals[month] = adjusted if report.measure == "net" else max(Decimal("0"), adjusted)
        rows = [ReportRow(m, m, totals.get(m, Decimal("0"))) for m in _months_between(start, end)]
        return ReportResult(report, currency, rows, sum((r.value for r in rows), Decimal("0")))

    base = with_category_lines(select().select_from(Transaction))
    if report.group_by == "category":
        key_col, label_col, color_col = Category.id, Category.name, Category.color
        stmt = base.outerjoin(Category, line_category_id() == Category.id)
    elif report.group_by == "category_group":
        key_col, label_col, color_col = CategoryGroup.id, CategoryGroup.name, CategoryGroup.color
        stmt = base.outerjoin(Category, line_category_id() == Category.id).outerjoin(
            CategoryGroup, Category.group_id == CategoryGroup.id
        )
    elif report.group_by == "payee":
        label_expr = func.coalesce(Payee.name, Transaction.payee)
        key_col, label_col, color_col = label_expr, label_expr, None
        stmt = base.outerjoin(Payee, Transaction.payee_id == Payee.id)
    else:  # account
        key_col, label_col, color_col = Account.id, Account.name, None
        stmt = base.join(Account, Transaction.account_id == Account.id)

    columns = [key_col, label_col, func.sum(value)] + ([color_col] if color_col is not None else [])
    group = [key_col, label_col] + ([color_col] if color_col is not None else [])
    result = await session.execute(stmt.add_columns(*columns).where(*filters).group_by(*group))
    rows = []
    for row in result.all():
        key, label, total = row[0], row[1], Decimal(str(row[2] or 0))
        color = row[3] if color_col is not None else None
        if total == 0:
            continue
        row_key = str(key) if key is not None else NONE_KEY
        credit, debit = offsets.get(row_key, (Decimal("0"), Decimal("0")))
        total -= _non_owner_offset(report.measure, credit, debit)
        if total == 0 or (report.measure != "net" and total < 0):
            continue
        rows.append(ReportRow(row_key, label if key is not None else None, total, color))
    rows.sort(key=lambda r: abs(r.value), reverse=True)
    if len(rows) > report.limit:
        kept, rest = rows[: report.limit - 1], rows[report.limit - 1 :]
        rows = kept + [ReportRow(OTHER_KEY, None, sum((r.value for r in rest), Decimal("0")))]
    return ReportResult(report, currency, rows, sum((r.value for r in rows), Decimal("0")))


# --- Code reports ---------------------------------------------------------------

#: Most transactions handed to a code report in one run.
MAX_INPUT_TRANSACTIONS = 20_000


class CodeReportUnavailable(Exception):
    """Plugins with code can't run here (no runner), or the runner can't be reached."""


class CodeReportFailed(Exception):
    """The plugin's report raised, timed out, or returned something that isn't a view."""


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


async def _transactions_input(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
    report_date: Any,
) -> dict[str, Any]:
    """The period's transactions as the workspace keeps them, split lines included.

    Each row carries `my_share_amount` and `my_share_amount_primary`: the
    viewer's part of it, so a code report adds up what the native figures
    count. A row the viewer paid with a group split counts only the viewer's
    share (the other members' shares come off, as in `run_report`); any
    other row counts whole, and `amount_primary` stays as stored.
    """
    from app.models.transaction_category_line import TransactionCategoryLine
    from app.models.transaction_split import TransactionSplit

    filters: list[Any] = [Transaction.workspace_id == workspace_id, report_date >= start, report_date <= end]
    if account_ids is not None:
        filters.append(Transaction.account_id.in_(account_ids))
    rows = (
        await session.execute(
            select(
                Transaction.id, report_date, Transaction.description, Transaction.amount, Transaction.type,
                Transaction.currency, Transaction.amount_primary, Transaction.category_id, Transaction.account_id,
                func.coalesce(Payee.name, Transaction.payee), Transaction.status, Transaction.source,
                Transaction.is_ignored, Transaction.user_id, Transaction.date,
            )
            .outerjoin(Payee, Transaction.payee_id == Payee.id)
            .where(*filters)
            .order_by(report_date, Transaction.id)
            .limit(MAX_INPUT_TRANSACTIONS + 1)
        )
    ).all()
    truncated = len(rows) > MAX_INPUT_TRANSACTIONS
    rows = rows[:MAX_INPUT_TRANSACTIONS]
    ids = [row[0] for row in rows]
    lines: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for chunk in range(0, len(ids), 1000):
        result = await session.execute(
            select(TransactionCategoryLine.transaction_id, TransactionCategoryLine.category_id,
                   TransactionCategoryLine.amount)
            .where(TransactionCategoryLine.transaction_id.in_(ids[chunk : chunk + 1000]))
            .order_by(TransactionCategoryLine.position)
        )
        for tx_id, category_id, amount in result.all():
            lines[tx_id].append({"category_id": str(category_id) if category_id else None, "amount": _money(amount)})

    viewer_member_ids = set((await session.execute(_viewer_member_ids(user_id))).scalars().all())
    others_by_tx: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    if viewer_member_ids:
        for chunk in range(0, len(ids), 1000):
            result = await session.execute(
                select(
                    TransactionSplit.transaction_id,
                    TransactionSplit.group_member_id,
                    TransactionSplit.share_amount,
                ).where(TransactionSplit.transaction_id.in_(ids[chunk : chunk + 1000]))
            )
            for tx_id, member_id, share in result.all():
                if member_id not in viewer_member_ids:
                    others_by_tx[tx_id] += Decimal(str(share or 0))

    rates: dict[tuple[str, date], Optional[Decimal]] = {}
    out: list[dict[str, Any]] = []
    for (
        tx_id, day, description, amount, kind, currency, amount_primary, category_id, account_id, payee,
        status, source, ignored, owner_id, tx_date,
    ) in rows:
        full = abs(Decimal(str(amount or 0)))
        others = others_by_tx.get(tx_id, Decimal("0")) if owner_id == user_id else Decimal("0")
        my_share = full - others
        my_share_primary = None if amount_primary is None else abs(Decimal(str(amount_primary)))
        if others and my_share_primary is not None:
            shared_primary = await _others_share_in_primary(
                session,
                (tx_id, category_id, kind, currency, amount, amount_primary, tx_date, others),
                primary_currency,
                rates,
            )
            if shared_primary is not None:
                my_share_primary -= shared_primary
        out.append(
            {
                "id": str(tx_id),
                "date": day.isoformat() if day else None,
                "description": description,
                "amount": _money(amount),
                "type": kind,
                "currency": currency,
                "amount_primary": _money(amount_primary),
                "my_share_amount": _money(my_share),
                "my_share_amount_primary": _money(my_share_primary),
                "category_id": str(category_id) if category_id else None,
                "category_lines": lines.get(tx_id, []),
                "account_id": str(account_id),
                "payee": payee,
                "status": status,
                "source": source,
                "is_ignored": bool(ignored),
            }
        )
    return {"rows": out, "truncated": truncated}


async def _accounts_input(session: AsyncSession, workspace_id: uuid.UUID, account_ids: Optional[list[uuid.UUID]]):
    from app.services.account_service import get_accounts

    picked = {str(a) for a in account_ids} if account_ids is not None else None
    return [
        {
            "id": str(a["id"]),
            "name": a.get("display_name") or a["name"],
            "type": a["type"],
            "currency": a["currency"],
            "balance": a["current_balance"],
            "institution": a.get("institution_name"),
            "connected": a.get("connection_id") is not None,
        }
        for a in await get_accounts(session, workspace_id)
        if picked is None or str(a["id"]) in picked
    ]


async def _categories_input(session: AsyncSession, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                Category.id, Category.name, Category.color, Category.icon, Category.group_id, CategoryGroup.name,
                Category.treat_as_transfer, Category.is_ignored, Category.source, Category.source_key,
            )
            .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
            .where(Category.workspace_id == workspace_id)
            .order_by(Category.name)
        )
    ).all()
    return [
        {
            "id": str(cid), "name": name, "color": color, "icon": icon,
            "group_id": str(group_id) if group_id else None, "group": group,
            "treat_as_transfer": bool(transfer), "is_ignored": bool(ignored),
            # A category the report's plugin created, by its key in the manifest.
            "plugin_key": key if source else None, "plugin": source,
        }
        for cid, name, color, icon, group_id, group, transfer, ignored, source, key in rows
    ]


async def run_code_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    record: WorkspacePlugin,
    report: Report,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]] = None,
    lang: str = "en",
) -> Any:
    """Run a plugin's own report in the runner and return the view it drew (ADR 0028).

    The plugin gets only the inputs its manifest names, read for this
    workspace with the period and accounts the viewer picked, and returns a
    view of blocks; what it returns is checked before anyone sees it.
    """
    from app.plugins import runner
    from app.plugins.report_view import InvalidView, parse_view

    if end < start or (end - start).days > MAX_DAYS:
        raise ValueError(f"the period must run forward and span at most {MAX_DAYS} days")
    if not runner.configured():
        raise CodeReportUnavailable("plugins with code need the plugin runner (the `plugins` profile)")
    user = await session.get(User, user_id)
    currency = user.primary_currency if user else get_settings().default_currency
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))

    data: dict[str, Any] = {}
    if "transactions" in report.inputs:
        data["transactions"] = await _transactions_input(
            session, workspace_id, user_id, currency, start, end, account_ids, report_date
        )
    if "accounts" in report.inputs:
        data["accounts"] = await _accounts_input(session, workspace_id, account_ids)
    if "categories" in report.inputs:
        data["categories"] = await _categories_input(session, workspace_id)
    params = {
        "report": report.key,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "today": app_today().isoformat(),
        "currency": currency,
        "lang": lang,
        "account_ids": [str(a) for a in account_ids] if account_ids is not None else None,
        "data": data,
    }
    securo = {"workspace_id": str(workspace_id), "report": report.key}

    async def call() -> Any:
        async with runner.RunnerClient() as client:
            return await client.call(record.plugin_id, "report", params, securo=securo, commit=record.commit)

    try:
        try:
            result = await call()
        except runner.RunnerRefused as exc:
            # The runner lost this commit (a new volume): put it back once, as a sync does.
            from app.plugins.connections import _reinstall

            if exc.status != 404 or not await _reinstall(record):
                raise
            result = await call()
    except runner.PluginCallFailed as exc:
        raise CodeReportFailed(f"{exc.kind}: {exc.message}"[:500]) from None
    except runner.RunnerRefused as exc:
        raise CodeReportUnavailable(f"the plugin runner refused the report ({exc.status})") from None
    except runner.RunnerUnavailable as exc:
        raise CodeReportUnavailable(str(exc)) from None
    try:
        view = parse_view(result)
    except InvalidView as exc:
        raise CodeReportFailed(str(exc)[:500]) from None
    if view.currency is None:
        view.currency = currency
    return view
