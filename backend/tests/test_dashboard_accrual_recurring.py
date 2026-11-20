"""Recurring projections on a credit card follow the accrual mode (#1379).

A card that closes on the 4th and is due on the 11th. Two monthly charges,
on the 17th and on the 27th: October's ones are already real rows, billed on
November 11; November's are still projections, billed on December 11.

In accrual mode a month holds the bills due in it, so November carries
October's real charges once and December carries November's projected ones.
Cash mode keeps every projection on its purchase date.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services import admin_service, budget_service, dashboard_service, report_service
from app.services.credit_card_service import compute_effective_date

TODAY = date(2026, 11, 12)
NOVEMBER = date(2026, 11, 1)
DECEMBER = date(2026, 12, 1)
CHARGES = (("Video subscription", Decimal("16.90"), 17), ("Coffee shop", Decimal("383.40"), 27))
OCTOBER_TOTAL = 400.30


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr("app.services.dashboard_service.app_today", lambda: TODAY)
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: TODAY)
    monkeypatch.setattr("app.services.report_service.app_today", lambda: TODAY)


async def _set_mode(session, mode: str) -> None:
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", mode)


@pytest_asyncio.fixture
async def card(session, test_user, test_workspace):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="card",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
        statement_close_day=4,
        payment_due_day=11,
    )
    session.add(account)
    await session.commit()
    return account


@pytest_asyncio.fixture
async def seeded(session, test_user, test_workspace, card, test_categories):
    category = test_categories[0]
    for description, amount, day in CHARGES:
        rec = RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=card.id,
            category_id=category.id,
            description=description,
            amount=amount,
            currency="BRL",
            type="debit",
            frequency="monthly",
            day_of_month=day,
            start_date=date(2026, 10, day),
            next_occurrence=date(2026, 11, day),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(rec)
        purchase = date(2026, 10, day)
        session.add(Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=card.id,
            category_id=category.id,
            recurring_transaction_id=rec.id,
            description=description,
            amount=amount,
            amount_primary=amount,
            currency="BRL",
            date=purchase,
            effective_date=compute_effective_date(purchase, 4, 11),
            type="debit",
            source="recurring",
            status="posted",
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()
    return category


@pytest.mark.asyncio
async def test_accrual_november_does_not_add_purchases_billed_in_december(
    session, test_user, test_workspace, seeded
):
    await _set_mode(session, "accrual")

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=NOVEMBER
    )
    projected = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, NOVEMBER
    )

    assert summary.monthly_expenses == pytest.approx(OCTOBER_TOTAL)
    assert summary.projected_expenses == pytest.approx(OCTOBER_TOTAL)
    assert projected == []


@pytest.mark.asyncio
async def test_accrual_december_holds_the_november_purchases_on_their_bill(
    session, test_user, test_workspace, seeded
):
    await _set_mode(session, "accrual")

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=DECEMBER
    )
    projected = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, DECEMBER
    )

    assert summary.projected_expenses == pytest.approx(OCTOBER_TOTAL)
    assert sorted((p.date, p.reporting_date) for p in projected) == [
        ("2026-11-17", "2026-12-11"),
        ("2026-11-27", "2026-12-11"),
    ]


@pytest.mark.asyncio
async def test_accrual_spending_and_budget_project_one_bill_per_month(
    session, test_user, test_workspace, seeded
):
    await _set_mode(session, "accrual")
    category_id = str(seeded.id)

    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, month=NOVEMBER
    )
    budget = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=NOVEMBER
    )

    row = next(s for s in spending if s.category_id == category_id)
    assert row.projected_total == pytest.approx(OCTOBER_TOTAL)
    line = next(b for b in budget if str(b.category_id) == category_id)
    assert line.projected_amount == Decimal("400.30")


@pytest.mark.asyncio
async def test_accrual_income_expenses_report_forecasts_one_bill_per_month(
    session, test_user, test_workspace, seeded
):
    await _set_mode(session, "accrual")
    # The report buckets with Postgres' to_char; SQLite needs a stand-in.
    raw = await session.connection()
    await raw.run_sync(
        lambda conn: conn.connection.dbapi_connection.create_function(
            "to_char", 2, lambda value, _fmt: None if value is None else str(value)[:7]
        )
    )

    report = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, currency="BRL"
    )

    november = next(point for point in report.trend if point.date == "2026-11")
    assert november.breakdowns["expenses"] == pytest.approx(OCTOBER_TOTAL)
    assert november.breakdowns["projectedExpenses"] == 0


@pytest.mark.asyncio
async def test_cash_mode_keeps_projections_on_the_purchase_date(
    session, test_user, test_workspace, seeded
):
    await _set_mode(session, "cash")

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=NOVEMBER
    )
    projected = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, NOVEMBER
    )

    assert summary.projected_expenses == pytest.approx(OCTOBER_TOTAL)
    assert sorted((p.date, p.reporting_date) for p in projected) == [
        ("2026-11-17", "2026-11-17"),
        ("2026-11-27", "2026-11-27"),
    ]


@pytest.mark.asyncio
async def test_accrual_ignores_the_cycle_of_a_card_moved_to_another_workspace(
    session, test_user, test_workspace, card, seeded
):
    # Sync moved the card away; its rules stay here. The other workspace's
    # billing setup must not date them, so they keep their purchase date.
    other = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal", default_currency="BRL", locale="en"
    )
    session.add(other)
    card.workspace_id = other.id
    await session.commit()
    await _set_mode(session, "accrual")

    projected = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, DECEMBER
    )

    assert sorted((p.date, p.reporting_date) for p in projected) == [
        ("2026-12-17", "2026-12-17"),
        ("2026-12-27", "2026-12-27"),
    ]


@pytest.mark.asyncio
async def test_accrual_daily_report_puts_a_projection_on_its_due_day(
    session, test_user, test_workspace, card, test_categories
):
    # A yearly charge on November 17 is billed on December 11. A daily
    # December 1-12 report must show it on the 11th, not on the 1st.
    category = test_categories[0]
    session.add(RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=card.id,
        category_id=category.id,
        description="Yearly plan",
        amount=Decimal("120.00"),
        currency="BRL",
        type="debit",
        frequency="yearly",
        day_of_month=17,
        start_date=date(2026, 11, 17),
        next_occurrence=date(2026, 11, 17),
        is_active=True,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    await _set_mode(session, "accrual")
    # The report buckets with Postgres' to_char; SQLite needs a stand-in.
    raw = await session.connection()
    await raw.run_sync(
        lambda conn: conn.connection.dbapi_connection.create_function(
            "to_char", 2, lambda value, fmt: None if value is None else str(value)[: len(fmt)]
        )
    )

    report = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, interval="daily", currency="BRL",
        start_date=DECEMBER, end_date=date(2026, 12, 12), include_forecasts=True,
    )

    points = {point.date: point for point in report.trend}
    assert points["2026-12-11"].breakdowns["projectedExpenses"] == pytest.approx(120.0)
    assert points["2026-12-01"].breakdowns["projectedExpenses"] == 0
    assert [c.value for c in points["2026-12-11"].composition] == [pytest.approx(120.0)]
    assert points["2026-12-01"].composition == []
