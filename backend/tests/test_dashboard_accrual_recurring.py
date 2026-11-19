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
