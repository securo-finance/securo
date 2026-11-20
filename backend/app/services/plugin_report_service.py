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

from sqlalchemy import case, func, select
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
) -> tuple[Manifest, Report]:
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
    return manifest, report


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
    if account_ids is not None:
        scope.append(Transaction.account_id.in_(account_ids))
    if report.categories:
        ids = await _category_ids(session, workspace_id, manifest, report.categories)
        if not ids:
            return ReportResult(report, currency, [], Decimal("0"))
        scope.append(line_category_id().in_(ids))
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

    if report.group_by == "month":
        stmt = with_category_lines(select(report_date, func.sum(value)).select_from(Transaction))
        result = await session.execute(stmt.where(*filters).group_by(report_date))
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for day, total in result.all():
            if day is not None:
                totals[_month(day)] += Decimal(str(total or 0))
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
        rows.append(ReportRow(str(key) if key is not None else NONE_KEY, label if key is not None else None, total, color))
    rows.sort(key=lambda r: abs(r.value), reverse=True)
    if len(rows) > report.limit:
        kept, rest = rows[: report.limit - 1], rows[report.limit - 1 :]
        rows = kept + [ReportRow(OTHER_KEY, None, sum((r.value for r in rest), Decimal("0")))]
    return ReportResult(report, currency, rows, sum((r.value for r in rows), Decimal("0")))
