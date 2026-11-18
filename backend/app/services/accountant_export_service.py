"""Accountant export: two CSV files inside one zip for a date range.

The archive is meant to open directly in spreadsheet and accounting tools, so
the files use the conventions those tools expect: UTF-8 with a BOM, semicolons
between fields, CRLF line ends, decimal commas and DD/MM/YYYY dates.

Two files are produced:

- ``transactions.csv``: one row per category line, so a split transaction
  appears once per category with that line's share. Both legs of a transfer
  are present, exactly as in the transaction list.
- ``profit-and-loss.csv``: income and expenses grouped by category, then the
  period result. It follows the shared definition of what counts as income or
  expense (``counts_as_user_pnl``), so transfers and ignored rows stay out.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.workspace_context import WorkspaceContext
from app.models.account import Account
from app.models.category import Category
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services._query_filters import (
    CATEGORY_LINE,
    counts_as_user_pnl,
    line_amount,
    line_category_id,
    line_primary_or_amount,
    reporting_date_col,
    with_category_lines,
)
from app.services.admin_service import get_credit_card_accounting_mode

TRANSACTIONS_FILE = "transactions.csv"
PROFIT_AND_LOSS_FILE = "profit-and-loss.csv"

TRANSACTION_HEADER = (
    "Date",
    "Account",
    "Amount",
    "Currency",
    "Category",
    "Payee",
    "Document",
    "Direction",
    "Status",
)
PROFIT_AND_LOSS_HEADER = ("Section", "Category", "Amount")

_INCOME = "income"
_EXPENSE = "expense"
_RESULT = "result"
_UNCATEGORIZED = "Uncategorized"


def workspace_slug(name: str) -> str:
    """A lowercase ASCII slug for the workspace name, used in the filename.

    Accents are folded first (``Sao`` from ``São``), then every run of
    non-alphanumeric characters becomes a single hyphen. A name with no ASCII
    letters or digits at all falls back to ``workspace`` so the filename is
    never empty.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "workspace"


def zip_filename(workspace_name: str, start_date: date, end_date: date) -> str:
    """``securo-accountant-<slug>-<start>_<end>.zip`` with ISO range dates."""
    return (
        f"securo-accountant-{workspace_slug(workspace_name)}-"
        f"{start_date.isoformat()}_{end_date.isoformat()}.zip"
    )


def _format_date(value: Any) -> str:
    """DD/MM/YYYY. SQLite may hand back an ISO string for a SQL expression."""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return value.strftime("%d/%m/%Y")


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _format_decimal(value: Any) -> str:
    """Two decimals and a decimal comma, e.g. ``1234.5`` -> ``1234,50``."""
    amount = _as_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:.2f}".replace(".", ",")


def _csv_bytes(header: Iterable[str], rows: Iterable[Iterable[str]]) -> bytes:
    """Write a semicolon-separated UTF-8 CSV with a BOM and CRLF endings.

    ``csv.QUOTE_MINIMAL`` quotes a field that contains the separator, a quote
    or a character from the line terminator, which is what the format needs
    without adding quotes to every field.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


async def _transaction_rows(
    session: AsyncSession,
    ctx: WorkspaceContext,
    start_date: date,
    end_date: date,
    accounting_mode: str,
) -> list[list[str]]:
    """One row per category line in the period, transfers included.

    The outer join over ``transaction_category_lines`` gives a plain row once
    and a split row once per line, so the line amount is the share that
    belongs to that category. Amounts are stored as positive magnitudes with
    ``type`` carrying the direction, so the sign is applied explicitly:
    positive for money in, negative for money out.
    """
    report_date = reporting_date_col(accounting_mode)

    statement = (
        with_category_lines(
            select(
                report_date.label("report_date"),
                Account.name.label("account"),
                func.abs(line_amount()).label("amount"),
                Transaction.currency.label("currency"),
                Category.name.label("category"),
                func.coalesce(Payee.name, Transaction.payee).label("payee"),
                # The transaction description is the document reference an
                # accountant reconciles against; a transaction carries no
                # separate fiscal document number.
                Transaction.description.label("document"),
                Transaction.type.label("type"),
                Transaction.status.label("status"),
                Transaction.id.label("transaction_id"),
                CATEGORY_LINE.position.label("position"),
            )
            .select_from(Transaction)
            .join(
                Account,
                and_(
                    Account.id == Transaction.account_id,
                    Account.workspace_id == ctx.id,
                ),
            )
        )
        # Joined after the category lines because both ON clauses read the
        # line-aware category expression. The workspace condition on each
        # join keeps a stray cross-workspace row from leaking a name.
        .outerjoin(
            Category,
            and_(
                Category.id == line_category_id(),
                Category.workspace_id == ctx.id,
            ),
        )
        .outerjoin(
            Payee,
            and_(
                Payee.id == Transaction.payee_id,
                Payee.workspace_id == ctx.id,
            ),
        )
        .where(
            Transaction.workspace_id == ctx.id,
            report_date >= start_date,
            report_date <= end_date,
        )
        .order_by(report_date, Transaction.id, CATEGORY_LINE.position)
    )

    exported: list[list[str]] = []
    for row in (await session.execute(statement)).all():
        direction = "in" if row.type == "credit" else "out"
        amount = abs(_as_decimal(row.amount))
        signed = amount if direction == "in" else -amount
        exported.append(
            [
                _format_date(row.report_date),
                row.account or "",
                _format_decimal(signed),
                row.currency or "",
                row.category or "",
                row.payee or "",
                row.document or "",
                direction,
                row.status or "",
            ]
        )
    return exported


async def _profit_and_loss_rows(
    session: AsyncSession,
    ctx: WorkspaceContext,
    start_date: date,
    end_date: date,
    accounting_mode: str,
) -> list[list[str]]:
    """Income and expenses per category in the workspace primary currency.

    ``counts_as_user_pnl`` is applied per joined part, so a split transaction
    contributes each line that counts and drops transfer-like or ignored parts
    on their own. Amounts use ``amount_primary`` when the row has one and fall
    back to the line amount otherwise.

    This books the full amount of every transaction, which is what the ledger
    says. The in-app income/expenses report is different on purpose: it shows
    the user's own share, so it subtracts the parts of their splits owed back
    by other people. The two numbers answer different questions and are not
    expected to match when a transaction is split with someone else.
    """
    report_date = reporting_date_col(accounting_mode)
    category_id = line_category_id()
    # A separate alias for the joined row: the P&L filter below reads the
    # category flags through a subquery on the base table, and an alias keeps
    # that subquery from being correlated with this join.
    report_category = aliased(Category, name="report_category")

    statement = (
        with_category_lines(
            select(
                Transaction.type.label("type"),
                category_id.label("category_id"),
                report_category.name.label("category_name"),
                func.sum(line_primary_or_amount()).label("total"),
            )
            .select_from(Transaction)
        )
        # Joined after the category lines because the ON clause reads the
        # line-aware category expression.
        .outerjoin(
            report_category,
            and_(
                report_category.id == category_id,
                report_category.workspace_id == ctx.id,
            ),
        )
        .where(
            Transaction.workspace_id == ctx.id,
            report_date >= start_date,
            report_date <= end_date,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_user_pnl(category_id),
        )
        .group_by(Transaction.type, category_id, report_category.name)
    )

    totals: dict[tuple[str, str], Decimal] = {}
    for row in (await session.execute(statement)).all():
        section = _INCOME if row.type == "credit" else _EXPENSE
        label = row.category_name or _UNCATEGORIZED
        total = _as_decimal(row.total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if section == _EXPENSE:
            total = abs(total)
        key = (section, label)
        totals[key] = totals.get(key, Decimal("0.00")) + total

    ordered: list[tuple[str, str, Decimal]] = [
        (section, label, total)
        for (section, label), total in totals.items()
    ]
    ordered.sort(key=lambda item: (0 if item[0] == _INCOME else 1, item[1].lower()))

    rows = [[section, label, _format_decimal(total)] for section, label, total in ordered]
    income_total = sum(
        (total for section, _, total in ordered if section == _INCOME), Decimal("0.00")
    )
    expense_total = sum(
        (total for section, _, total in ordered if section == _EXPENSE), Decimal("0.00")
    )
    rows.append([_RESULT, "", _format_decimal(income_total - expense_total)])
    return rows


async def build_accountant_export(
    session: AsyncSession,
    ctx: WorkspaceContext,
    start_date: date,
    end_date: date,
) -> bytes:
    """Return the zip bytes holding the two CSV files for the period."""
    accounting_mode = await get_credit_card_accounting_mode(session)
    transactions = await _transaction_rows(
        session, ctx, start_date, end_date, accounting_mode
    )
    profit_and_loss = await _profit_and_loss_rows(
        session, ctx, start_date, end_date, accounting_mode
    )

    return _zip_bytes(
        {
            TRANSACTIONS_FILE: _csv_bytes(TRANSACTION_HEADER, transactions),
            PROFIT_AND_LOSS_FILE: _csv_bytes(PROFIT_AND_LOSS_HEADER, profit_and_loss),
        }
    )
