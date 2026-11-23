"""Reimbursements in the P&L views: dashboard, budgets, reports (#1126).

A dinner of 100 paid back with 66 must show 34 of spending and no income
wherever user-level P&L is shown, while balances keep every row whole.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import budget_service, dashboard_service, reimbursement_service

TODAY = app_today()
MONTH = TODAY.replace(day=1)


@pytest.fixture
async def scene(session, test_user, test_account, test_workspace):
    """A dinner of 100 in Food, paid back 33 + 33 by friends."""

    async def category(name):
        cat = Category(
            user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, icon="x", color="#000000",
        )
        session.add(cat)
        await session.flush()
        return cat

    async def tx(amount, type_, category_id, **extra):
        row = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description="Row", amount=Decimal(amount),
            currency=test_account.currency, date=extra.pop("date", MONTH), type=type_,
            source="manual", category_id=category_id, **extra,
        )
        session.add(row)
        await session.flush()
        return row

    food = await category("Food")
    paybacks = await category("Paybacks")
    dinner = await tx("100", "debit", food.id)
    friends = [await tx("33", "credit", paybacks.id) for _ in range(2)]
    await reimbursement_service.replace_allocations(
        session,
        dinner,
        [ReimbursementAllocationInput(counterpart_id=f.id, amount=Decimal("33")) for f in friends],
    )
    await session.commit()
    return {"food": food, "paybacks": paybacks, "dinner": dinner, "friends": friends, "tx": tx}


@pytest.mark.asyncio
async def test_dashboard_summary_counts_only_the_net(session, test_user, test_workspace, scene):
    summary = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)

    assert summary.monthly_expenses == pytest.approx(34.0)
    assert summary.monthly_income == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dashboard_spending_by_category_nets_the_anchor(
    session, test_user, test_workspace, scene
):
    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id
    )

    assert {row.category_id: row.total for row in spending} == {
        str(scene["food"].id): pytest.approx(34.0)
    }


@pytest.mark.asyncio
async def test_monthly_trend_counts_only_the_net(session, test_user, test_workspace, scene):
    trend = await dashboard_service.get_monthly_trend(
        session, test_workspace.id, test_user.id, months=1
    )

    current = next(row for row in trend if row.month == MONTH.strftime("%Y-%m"))
    assert (current.income, current.expenses) == (pytest.approx(0.0), pytest.approx(34.0))


@pytest.mark.asyncio
async def test_budget_actual_is_the_net(session, test_user, test_workspace, scene):
    actual = await budget_service.get_budget_vs_actual(session, test_workspace.id, test_user.id)

    food = next(row for row in actual if row.category_id == scene["food"].id)
    assert food.actual_amount == Decimal("34")


@pytest.mark.asyncio
async def test_budget_counts_a_linked_refund_in_the_same_category_once(
    session, test_user, test_workspace, scene
):
    # A purchase of 80 in Food, refunded 30 under Food too: the budget nets
    # the refund already, and the link must not take it off a second time.
    purchase = await scene["tx"]("80", "debit", scene["food"].id)
    refund = await scene["tx"]("30", "credit", scene["food"].id)
    await reimbursement_service.replace_allocations(
        session, purchase, [ReimbursementAllocationInput(counterpart_id=refund.id, amount=Decimal("30"))]
    )
    await session.commit()

    actual = await budget_service.get_budget_vs_actual(session, test_workspace.id, test_user.id)

    food = next(row for row in actual if row.category_id == scene["food"].id)
    assert food.actual_amount == Decimal("84")


@pytest.mark.asyncio
async def test_balances_keep_every_row_whole(session, test_user, test_workspace, test_account):
    from app.models.account import Account
    from app.services.account_service import get_account_with_balances

    # The dinner on one account and its paybacks on another: a netted balance
    # would move money between them, the real one keeps -100 and +66. (Manual
    # accounts: a balance is the sum of the rows, not a provider's snapshot.)
    test_account.connection_id = None
    wallet = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Savings",
        type="checking", balance=0, currency=test_account.currency,
    )
    session.add(wallet)
    await session.flush()
    dinner = await _row(session, test_user, test_account, test_workspace, "100", "debit")
    alice = await _row(session, test_user, wallet, test_workspace, "33", "credit")
    bob = await _row(session, test_user, wallet, test_workspace, "33", "credit")
    await reimbursement_service.replace_allocations(
        session, dinner,
        [ReimbursementAllocationInput(counterpart_id=row.id, amount=Decimal("33")) for row in (alice, bob)],
    )
    await session.commit()

    spent = await get_account_with_balances(session, test_account.id, test_workspace.id)
    received = await get_account_with_balances(session, wallet.id, test_workspace.id)

    assert spent is not None and received is not None
    assert Decimal(str(spent["current_balance"])) == Decimal("-100")
    assert Decimal(str(received["current_balance"])) == Decimal("66")


@pytest.mark.asyncio
async def test_a_pending_payback_nets_the_projection(session, test_user, test_workspace, scene):
    dinner = await scene["tx"]("50", "debit", scene["food"].id)
    pending = await scene["tx"](
        "20", "credit", scene["paybacks"].id, status="pending", date=TODAY + timedelta(days=1)
    )
    await reimbursement_service.replace_allocations(
        session, dinner, [ReimbursementAllocationInput(counterpart_id=pending.id, amount=Decimal("20"))]
    )
    await session.commit()

    summary = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)

    # Posted: 34 from the first dinner, 30 from this one. The pending
    # payback adds nothing to projected income.
    assert summary.monthly_expenses == pytest.approx(64.0)
    assert summary.projected_income == pytest.approx(summary.monthly_income)


def _trend_totals(report) -> dict[tuple[str, str], float]:
    return {(item.key, item.group): item.total for item in report.category_trend}


@pytest.mark.asyncio
async def test_income_expenses_report_counts_only_the_net(
    session, test_user, test_workspace, scene
):
    from app.services.report_service import get_income_expenses_report

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1
    )

    current = next(p for p in report.trend if p.date == MONTH.strftime("%Y-%m"))
    assert current.breakdowns["income"] == pytest.approx(0.0)
    assert current.breakdowns["expenses"] == pytest.approx(34.0)
    assert _trend_totals(report) == {(str(scene["food"].id), "expenses"): pytest.approx(34.0)}


@pytest.mark.asyncio
async def test_report_counts_an_over_reimbursed_anchor_as_income_in_its_category(
    session, test_user, test_workspace, scene
):
    from app.services.report_service import get_income_expenses_report

    gifts_tx = await scene["tx"]("100", "debit", scene["food"].id)
    payback = await scene["tx"]("150", "credit", scene["paybacks"].id)
    await reimbursement_service.replace_allocations(
        session, gifts_tx, [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("150"))]
    )
    await session.commit()

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1
    )

    current = next(p for p in report.trend if p.date == MONTH.strftime("%Y-%m"))
    assert current.breakdowns["income"] == pytest.approx(50.0)
    assert current.breakdowns["expenses"] == pytest.approx(34.0)
    assert _trend_totals(report) == {
        (str(scene["food"].id), "expenses"): pytest.approx(34.0),
        (str(scene["food"].id), "income"): pytest.approx(50.0),
    }


@pytest.mark.asyncio
async def test_category_statement_counts_only_the_net(session, test_user, test_workspace, scene):
    from app.services.category_statement_service import get_category_statement

    statement = await get_category_statement(
        session, test_workspace.id, test_user.id, MONTH, months=1, primary_currency="BRL"
    )

    def flatten(rows):
        for row in rows:
            yield row
            yield from flatten(row.children)

    values = {
        (section, row.label): row.values[0]
        for section, part in (("income", statement.income), ("expenses", statement.expenses))
        for row in flatten(part.rows)
        if row.kind == "category" and row.values[0]
    }
    assert values == {("expenses", "Food"): pytest.approx(-34.0)}


async def _row(session, user, account, workspace, amount, type_, category_id=None, **extra):
    row = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=account.id,
        description="Row", amount=Decimal(amount), currency=account.currency,
        date=extra.pop("date", MONTH), type=type_, source="manual", category_id=category_id,
        **extra,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_budget_nets_a_category_whose_raw_total_is_below_zero(
    session, test_user, test_workspace, test_account
):
    # Category B holds an 80 expense and a 100 payback that pays an expense in
    # A: its raw net is -20, but once the payback comes off it spent 80.
    shopping = Category(user_id=test_user.id, workspace_id=test_workspace.id, name="Shopping", icon="x", color="#000000")
    cafe = Category(user_id=test_user.id, workspace_id=test_workspace.id, name="Cafe", icon="x", color="#000000")
    session.add_all([shopping, cafe])
    await session.flush()
    anchor = await _row(session, test_user, test_account, test_workspace, "100", "debit", shopping.id)
    await _row(session, test_user, test_account, test_workspace, "80", "debit", cafe.id)
    payback = await _row(session, test_user, test_account, test_workspace, "100", "credit", cafe.id)
    await reimbursement_service.replace_allocations(
        session, anchor, [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("100"))]
    )
    await session.commit()

    actual = {
        row.category_id: row.actual_amount
        for row in await budget_service.get_budget_vs_actual(session, test_workspace.id, test_user.id)
    }

    assert actual[cafe.id] == Decimal("80")
    assert actual.get(shopping.id, Decimal("0")) == Decimal("0")


@pytest.mark.asyncio
async def test_a_workspace_total_nets_another_members_rows_too(
    session, test_user, test_superuser, test_workspace, test_account
):
    food = Category(user_id=test_user.id, workspace_id=test_workspace.id, name="Food", icon="x", color="#000000")
    session.add(food)
    await session.flush()
    # Bob's dinner and the payback he got, in Alice's workspace.
    dinner = await _row(session, test_superuser, test_account, test_workspace, "100", "debit", food.id)
    payback = await _row(session, test_superuser, test_account, test_workspace, "60", "credit")
    await reimbursement_service.replace_allocations(
        session, dinner, [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("60"))]
    )
    await session.commit()

    summary = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)

    assert summary.monthly_expenses == pytest.approx(40.0)
    assert summary.monthly_income == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_pending_anchor_paid_back_in_excess_never_projects_a_negative_expense(
    session, test_user, test_workspace, test_account
):
    from app.services._query_filters import forecast_owner_share_fractions

    anchor = await _row(session, test_user, test_account, test_workspace, "100", "debit", status="pending")
    payback = await _row(session, test_user, test_account, test_workspace, "150", "credit", status="pending")
    await reimbursement_service.replace_allocations(
        session, anchor, [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("150"))]
    )
    await session.commit()

    fractions = await forecast_owner_share_fractions(session, test_user.id, [anchor, payback])

    assert fractions[anchor.id] == Decimal("0")
    assert fractions[payback.id] == Decimal("0")


@pytest.mark.asyncio
async def test_the_report_nets_rows_dated_after_today_that_its_window_counts(
    session, test_user, test_workspace, test_account
):
    from app.services.report_service import get_income_expenses_report

    # A posted expense dated tomorrow and its posted payback dated today, in a
    # window that runs past today: the report counts both rows, so it has to
    # take the link off both.
    expense = await _row(
        session, test_user, test_account, test_workspace, "100", "debit", date=TODAY + timedelta(days=1)
    )
    payback = await _row(session, test_user, test_account, test_workspace, "50", "credit", date=TODAY)
    await reimbursement_service.replace_allocations(
        session, expense, [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("50"))]
    )
    await session.commit()

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, interval="daily",
        start_date=TODAY, end_date=TODAY + timedelta(days=2),
    )

    income = sum(point.breakdowns["income"] for point in report.trend)
    expenses = sum(point.breakdowns["expenses"] for point in report.trend)
    assert (income, expenses) == (pytest.approx(0.0), pytest.approx(50.0))
