from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.debt import DebtCreate, DebtPlanCreate, DebtStrategySettingUpdate
from app.services import debt_payoff_strategy_service, debt_service


async def _make_debt(session, workspace, user, *, balance, rate, installment, collection_mode="manual"):
    debt = await debt_service.create_debt(
        session,
        workspace.id,
        user.id,
        DebtCreate(
            kind="loan",
            creditor_name=f"Credor {balance}",
            original_principal=balance,
            current_balance=balance,
            currency="BRL",
            opened_date=date(2026, 1, 1),
        ),
    )
    await debt_service.create_debt_plan(
        session,
        debt.id,
        workspace.id,
        DebtPlanCreate(
            kind="original_contract",
            collection_mode=collection_mode,
            interest_rate=rate,
            installment_amount=installment,
            num_installments=60,
            first_due_date=date(2026, 2, 1),
            frequency="monthly",
            activate=True,
        ),
    )
    return debt


@pytest.mark.asyncio
async def test_default_strategy_setting_is_avalanche_no_extra(
    session: AsyncSession, test_workspace: Workspace
):
    setting = await debt_payoff_strategy_service.get_or_create_strategy_setting(session, test_workspace.id)
    assert setting.method == "avalanche"
    assert setting.extra_monthly_amount == Decimal("0")


@pytest.mark.asyncio
async def test_update_strategy_setting_persists(session: AsyncSession, test_workspace: Workspace):
    await debt_payoff_strategy_service.update_strategy_setting(
        session,
        test_workspace.id,
        DebtStrategySettingUpdate(method="snowball", extra_monthly_amount=Decimal("100.00")),
    )
    setting = await debt_payoff_strategy_service.get_or_create_strategy_setting(session, test_workspace.id)
    assert setting.method == "snowball"
    assert setting.extra_monthly_amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_snowball_rolls_extra_to_next_smallest_debt(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    small = await _make_debt(
        session, test_workspace, test_user, balance=Decimal("500.00"), rate=Decimal("0"), installment=Decimal("100.00")
    )
    large = await _make_debt(
        session, test_workspace, test_user, balance=Decimal("1000.00"), rate=Decimal("0"), installment=Decimal("100.00")
    )
    await debt_payoff_strategy_service.update_strategy_setting(
        session,
        test_workspace.id,
        DebtStrategySettingUpdate(method="snowball", extra_monthly_amount=Decimal("100.00")),
    )

    projection = await debt_payoff_strategy_service.compute_payoff_projection(session, test_workspace.id)

    assert [e.debt_id for e in projection.order] == [small.id, large.id]
    by_id = {e.debt_id: e for e in projection.order}
    assert by_id[small.id].months_to_payoff == 3
    assert by_id[large.id].months_to_payoff == 7
    assert projection.overall_payoff_date is not None


@pytest.mark.asyncio
async def test_avalanche_orders_by_interest_rate_not_balance(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    high_rate = await _make_debt(
        session, test_workspace, test_user, balance=Decimal("1000.00"), rate=Decimal("5"), installment=Decimal("150.00")
    )
    low_rate = await _make_debt(
        session, test_workspace, test_user, balance=Decimal("500.00"), rate=Decimal("1"), installment=Decimal("100.00")
    )
    await debt_payoff_strategy_service.update_strategy_setting(
        session,
        test_workspace.id,
        DebtStrategySettingUpdate(method="avalanche", extra_monthly_amount=Decimal("50.00")),
    )

    projection = await debt_payoff_strategy_service.compute_payoff_projection(session, test_workspace.id)

    assert [e.debt_id for e in projection.order] == [high_rate.id, low_rate.id]


@pytest.mark.asyncio
async def test_extra_payment_never_applied_to_payroll_deduction_debt(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    payroll = await _make_debt(
        session,
        test_workspace,
        test_user,
        balance=Decimal("300.00"),
        rate=Decimal("0"),
        installment=Decimal("100.00"),
        collection_mode="payroll_deduction",
    )
    manual = await _make_debt(
        session, test_workspace, test_user, balance=Decimal("1000.00"), rate=Decimal("0"), installment=Decimal("100.00")
    )
    await debt_payoff_strategy_service.update_strategy_setting(
        session,
        test_workspace.id,
        DebtStrategySettingUpdate(method="snowball", extra_monthly_amount=Decimal("100.00")),
    )

    projection = await debt_payoff_strategy_service.compute_payoff_projection(session, test_workspace.id)
    by_id = {e.debt_id: e for e in projection.order}

    # Payroll debt pays off on its own fixed schedule (3 months at 100/mo,
    # never accelerated); the manual debt receives the full extra every
    # month instead, finishing in 5 rather than 10 months.
    assert by_id[payroll.id].months_to_payoff == 3
    assert by_id[manual.id].months_to_payoff == 5
