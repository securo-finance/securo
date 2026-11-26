"""The income-expenses report reads the window once per source.

Issue #584: a 12-month income-expenses report aggregated the same window
twice, once for the summary and once for the category trend, and walked the
window month by month for recurring projections, forecast rows and open
claims, re-reading the same recurring rules for every month and returning no
rows on most of them.

These tests pin what the report reads and prove the numbers did not move: the
summary is the rollup of the category trend, and every prediction source
answers the whole window in a single call.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import dashboard_service, invoice_forecast_service, report_service
from app.services.dashboard_service import _get_forecast_transactions, _get_recurring_projections
from app.services.report_service import get_income_expenses_report


async def _make_account(session: AsyncSession, user_id: uuid.UUID, name: str) -> Account:
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, name=name, type="checking",
        balance=Decimal("0.00"), currency="BRL",
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


async def _add_txn(
    session: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID,
    amount: float, txn_type: str, txn_date: date, *,
    source: str = "manual", category_id: uuid.UUID | None = None,
    status: str = "posted", is_ignored: bool = False,
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        category_id=category_id, description=f"Test {txn_type} {amount}",
        amount=Decimal(str(amount)), date=txn_date, type=txn_type,
        source=source, status=status, currency="BRL",
        effective_date=txn_date, is_ignored=is_ignored,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _make_category(session: AsyncSession, user_id: uuid.UUID, name: str) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, name=name, icon="tag",
        color="#123456", is_system=False,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _make_recurring(
    session: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID,
    amount: float, txn_type: str, next_occurrence: date,
    category_id: uuid.UUID | None = None, frequency: str = "monthly",
) -> RecurringTransaction:
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        category_id=category_id, description=f"Rec {txn_type} {amount}",
        amount=Decimal(str(amount)), currency="BRL", type=txn_type,
        frequency=frequency, start_date=next_occurrence,
        next_occurrence=next_occurrence, is_active=True,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


def _label(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


@pytest.mark.asyncio
async def test_summary_totals_are_the_rollup_of_the_window_aggregate(
    session: AsyncSession, test_user, test_workspace,
):
    """Income and expenses per period are still exactly the sum of the rows.

    The summary used to run its own aggregate and now rolls up the per-category
    one. A negative credit, a zero amount, an ignored row, a pending row and an
    opening balance all keep counting (or not) as they did, and the total is
    summed in Decimal so it matches what the database aggregate returned.
    """
    acct = await _make_account(session, test_user.id, "IE Rollup")
    salary = await _make_category(session, test_user.id, "Salary")
    food = await _make_category(session, test_user.id, "Food")
    today = date.today()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)

    rows = [
        (3000.0, "credit", this_month, salary.id, "posted", False, "manual"),
        (400.0, "debit", this_month, food.id, "posted", False, "manual"),
        # A refund stored as a negative credit, and a zero row: both counted
        # the same way the aggregate counted them.
        (-120.0, "credit", this_month, salary.id, "posted", False, "manual"),
        (0.0, "debit", this_month, food.id, "posted", False, "manual"),
        # Neither of these is actual P&L.
        (999.0, "debit", this_month, food.id, "posted", True, "manual"),
        (55.0, "debit", this_month, food.id, "pending", False, "manual"),
        (5000.0, "credit", this_month, salary.id, "posted", False, "opening_balance"),
        (2500.0, "credit", last_month, salary.id, "posted", False, "manual"),
        (700.0, "debit", last_month, food.id, "posted", False, "manual"),
    ]
    for amount, txn_type, when, cat_id, status, ignored, source in rows:
        await _add_txn(
            session, test_user.id, acct.id, amount, txn_type, when,
            category_id=cat_id, status=status, is_ignored=ignored, source=source,
        )

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=3, interval="monthly",
    )

    expected: dict[str, list[float]] = {}
    for amount, txn_type, when, _cat, status, ignored, source in rows:
        if status != "posted" or ignored or source == "opening_balance":
            continue
        bucket = expected.setdefault(_label(when), [0.0, 0.0])
        bucket[0 if txn_type == "credit" else 1] += amount

    by_label = {point.date: point.breakdowns for point in report.trend}
    for label, (income, expenses) in expected.items():
        assert by_label[label]["income"] == pytest.approx(income), label
        assert by_label[label]["expenses"] == pytest.approx(abs(expenses)), label
    # A pending or opening row never lands in the actual summary.
    summary = {b.key: b.value for b in report.summary.breakdowns}
    assert summary["income"] == pytest.approx(3000.0 - 120.0 + 2500.0)
    assert summary["expenses"] == pytest.approx(400.0 + 700.0)


@pytest.mark.asyncio
async def test_every_forecast_source_reads_the_window_once(
    session: AsyncSession, test_user, test_workspace,
):
    """A 12-month report calls each source once, not once per month."""
    acct = await _make_account(session, test_user.id, "IE Sources")
    cat = await _make_category(session, test_user.id, "Rent")
    today = date.today()
    await _make_recurring(
        session, test_user.id, acct.id, 800, "debit",
        next_occurrence=today.replace(day=1), category_id=cat.id,
    )

    calls = {"recurring": [], "forecast": [], "claims": []}

    async def recurring(*args, **kwargs):
        calls["recurring"].append((args[2], args[3]))
        return await _get_recurring_projections(*args, **kwargs)

    async def forecast(*args, **kwargs):
        calls["forecast"].append((args[2], args[3]))
        return await _get_forecast_transactions(*args, **kwargs)

    original_claims = invoice_forecast_service.claims_in_range

    async def claims(*args, **kwargs):
        calls["claims"].append((args[2], args[3]))
        return await original_claims(*args, **kwargs)

    with (
        patch.object(dashboard_service, "_get_recurring_projections", recurring),
        patch.object(report_service, "_get_forecast_transactions", forecast),
        patch.object(invoice_forecast_service, "claims_in_range", claims),
    ):
        await get_income_expenses_report(
            session, test_workspace.id, test_user.id, months=12, interval="monthly",
        )

    # One call per source for the whole report: the summary and the category
    # composition read the same rows, never one read per month and section.
    assert len(calls["recurring"]) == 1, calls["recurring"]
    assert len(calls["forecast"]) == 1, calls["forecast"]
    assert len(calls["claims"]) == 1, calls["claims"]
    for range_start, range_end in calls["recurring"] + calls["forecast"] + calls["claims"]:
        assert range_start <= today
        assert range_end > today
        # The read starts on the first day of the window's first month, the
        # superset the category composition covers.
        assert range_start.day == 1


@pytest.mark.asyncio
async def test_a_mid_month_window_keeps_the_summary_start(
    session: AsyncSession, test_user, test_workspace,
):
    """The shared read starts on the first of the month; the summary keeps its own start.

    The reads cover whole months for the composition, so the summary has to
    drop the forecast rows before the day it was asked for. A projection on
    the first of last month belongs to the composition but not to a window
    that starts in the middle of that month.
    """
    acct = await _make_account(session, test_user.id, "IE Mid Month")
    cat = await _make_category(session, test_user.id, "Rent")
    first_of_last_month = (
        date.today().replace(day=1) - timedelta(days=1)
    ).replace(day=1)
    await _make_recurring(
        session, test_user.id, acct.id, 800, "debit",
        next_occurrence=first_of_last_month, category_id=cat.id,
    )

    start = first_of_last_month + timedelta(days=14)
    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly",
        start_date=start, end_date=date.today(), include_forecasts=True,
    )

    # Two occurrences were read (last month and this one); only the one on or
    # after the requested start counts in the summary.
    summary = {b.key: b.value for b in report.summary.breakdowns}
    assert summary["projectedExpenses"] == pytest.approx(800.0)
    slices = {(item.key, item.group): item for item in report.composition}
    rent = [item for item in slices.values() if item.label == "Rent"]
    assert len(rent) == 1
    assert rent[0].value == pytest.approx(1600.0)



@pytest.mark.asyncio
async def test_the_window_is_aggregated_once(
    session: AsyncSession, test_user, test_workspace,
):
    """One statement groups the window for the summary and the trend together.

    The report used to run the same aggregate twice: one grouping by period for
    the summary, one by period and category for the trend.
    """
    acct = await _make_account(session, test_user.id, "IE Aggregate")
    cat = await _make_category(session, test_user.id, "Groceries")
    today = date.today()
    await _add_txn(
        session, test_user.id, acct.id, 90, "debit", today, category_id=cat.id,
    )
    await _add_txn(
        session, test_user.id, acct.id, 900, "credit", today, category_id=cat.id,
    )

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = await session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        await get_income_expenses_report(
            session, test_workspace.id, test_user.id, months=12, interval="monthly",
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)

    # The summary and the trend were the two statements joining the category
    # lines, one grouping by period and one by period and category.
    window_aggregates = [
        statement for statement in statements
        if "GROUP BY" in statement.upper() and "category_line.id" in statement
    ]
    assert len(window_aggregates) == 1, len(window_aggregates)


@pytest.mark.asyncio
async def test_a_weekly_rule_keeps_every_occurrence_of_a_long_window(
    session: AsyncSession, test_user, test_workspace,
):
    """Reading the window once must not hit a cap the monthly walk never reached.

    ``get_occurrences_in_range`` stopped after 201 occurrences. Walking the
    window month by month the report never asked for enough dates to reach it;
    reading the whole window in one call did, and both the summary and the
    composition stopped at 201, dropping the rest of a multi-year window
    without any warning (#1904).
    """
    acct = await _make_account(session, test_user.id, "IE Long Window")
    cat = await _make_category(session, test_user.id, "Rent")
    today = date.today()
    start = today - timedelta(days=1461)
    await _make_recurring(
        session, test_user.id, acct.id, 100, "debit",
        next_occurrence=start, category_id=cat.id, frequency="weekly",
    )

    window_end = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    occurrences = 0
    cursor = start
    while cursor < window_end:
        occurrences += 1
        cursor += timedelta(weeks=1)
    # The requested range holds more occurrences than the old cap, so the
    # head before this fix reported 201 of them and this test fails there.
    assert occurrences > 201

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly",
        start_date=start, end_date=today, include_forecasts=True,
    )

    expected = 100.0 * occurrences
    summary = {b.key: b.value for b in report.summary.breakdowns}
    assert summary["projectedExpenses"] == pytest.approx(expected)
    slices = {(item.key, item.group): item for item in report.composition}
    rent = [item for item in slices.values() if item.label == "Rent"]
    assert len(rent) == 1
    assert rent[0].value == pytest.approx(expected)
