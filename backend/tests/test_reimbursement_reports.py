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
async def test_balances_keep_every_row_whole(session, test_user, test_workspace, test_account, scene):
    from app.services.account_service import get_account_with_balances

    # A manual account: its balance is the sum of its rows (-100 + 33 + 33),
    # not the provider's.
    test_account.connection_id = None
    await session.commit()
    account = await get_account_with_balances(session, test_account.id, test_workspace.id)

    assert account is not None
    assert Decimal(str(account["current_balance"])) == Decimal("-34")


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
