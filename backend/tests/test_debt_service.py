import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.debt import DebtCreate, DebtPlanCreate
from app.services import debt_service


def _debt_payload(**overrides) -> DebtCreate:
    payload = dict(
        kind="loan",
        creditor_name="Banco Teste",
        original_principal=Decimal("1000.00"),
        current_balance=Decimal("1000.00"),
        currency="BRL",
        opened_date=date(2026, 1, 1),
    )
    payload.update(overrides)
    return DebtCreate(**payload)


@pytest.mark.asyncio
async def test_build_amortization_schedule_sums_to_principal():
    rows = debt_service.build_amortization_schedule(
        principal=Decimal("1000.00"),
        periodic_rate=Decimal("0.02"),
        installment_amount=Decimal("300.00"),
        num_installments=4,
    )
    assert len(rows) == 4
    assert rows[0]["interest_portion"] == Decimal("20.00")
    assert sum(r["principal_portion"] for r in rows) == Decimal("1000.00")
    # Last installment forces the balance to exactly zero.
    assert rows[-1]["principal_portion"] + sum(r["principal_portion"] for r in rows[:-1]) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_build_amortization_schedule_zero_interest_splits_evenly():
    rows = debt_service.build_amortization_schedule(
        principal=Decimal("1000.00"),
        periodic_rate=Decimal("0"),
        installment_amount=Decimal("250.00"),
        num_installments=4,
    )
    assert [r["amount"] for r in rows] == [Decimal("250.00")] * 4
    assert all(r["interest_portion"] == Decimal("0.00") for r in rows)


@pytest.mark.asyncio
async def test_create_debt_and_fetch(session: AsyncSession, test_user: User, test_workspace: Workspace):
    debt = await debt_service.create_debt(session, test_workspace.id, test_user.id, _debt_payload())
    fetched = await debt_service.get_debt(session, debt.id, test_workspace.id)
    assert fetched is not None
    assert fetched.creditor_name == "Banco Teste"
    assert fetched.current_balance == Decimal("1000.00")

    all_debts = await debt_service.get_debts(session, test_workspace.id)
    assert [d.id for d in all_debts] == [debt.id]


@pytest.mark.asyncio
async def test_debt_scoped_to_workspace(session: AsyncSession, test_user: User, test_workspace: Workspace):
    await debt_service.create_debt(session, test_workspace.id, test_user.id, _debt_payload())
    other_workspace_id = uuid.uuid4()
    assert await debt_service.get_debts(session, other_workspace_id) == []


@pytest.mark.asyncio
async def test_create_plan_generates_installments(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt = await debt_service.create_debt(session, test_workspace.id, test_user.id, _debt_payload())
    plan = await debt_service.create_debt_plan(
        session,
        debt.id,
        test_workspace.id,
        DebtPlanCreate(
            kind="original_contract",
            collection_mode="manual",
            interest_rate=Decimal("0"),
            installment_amount=Decimal("250.00"),
            num_installments=4,
            first_due_date=date(2026, 2, 5),
            frequency="monthly",
            activate=True,
        ),
    )
    assert plan.status == "active"
    assert len(plan.installments) == 4
    assert [i.due_date.month for i in plan.installments] == [2, 3, 4, 5]
    assert all(i.status == "pending" for i in plan.installments)


@pytest.mark.asyncio
async def test_activating_new_plan_supersedes_previous_active(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt = await debt_service.create_debt(session, test_workspace.id, test_user.id, _debt_payload())
    base_plan_args = dict(
        kind="original_contract",
        collection_mode="manual",
        interest_rate=Decimal("0"),
        installment_amount=Decimal("250.00"),
        num_installments=4,
        first_due_date=date(2026, 2, 5),
        frequency="monthly",
    )
    first = await debt_service.create_debt_plan(
        session, debt.id, test_workspace.id, DebtPlanCreate(**base_plan_args, activate=True)
    )
    second = await debt_service.create_debt_plan(
        session,
        debt.id,
        test_workspace.id,
        DebtPlanCreate(**{**base_plan_args, "kind": "renegotiated"}, activate=True),
    )
    await session.refresh(first)
    assert first.status == "superseded"
    assert second.status == "active"


@pytest.mark.asyncio
async def test_simulation_plan_does_not_activate(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt = await debt_service.create_debt(session, test_workspace.id, test_user.id, _debt_payload())
    plan = await debt_service.create_debt_plan(
        session,
        debt.id,
        test_workspace.id,
        DebtPlanCreate(
            kind="simulation",
            collection_mode="manual",
            interest_rate=Decimal("1.5"),
            installment_amount=Decimal("260.00"),
            num_installments=4,
            first_due_date=date(2026, 2, 5),
            frequency="monthly",
            activate=False,
        ),
    )
    assert plan.status == "proposed"
    active = await debt_service.get_active_plan(session, debt.id, test_workspace.id)
    assert active is None
