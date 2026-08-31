import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.debt import Debt, DebtInstallment, DebtPlan
from app.schemas.debt import DebtCreate, DebtPlanCreate, DebtUpdate
from app.services.recurring_transaction_service import _advance_date

TWO_PLACES = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def build_amortization_schedule(
    principal: Decimal,
    periodic_rate: Decimal,
    installment_amount: Decimal,
    num_installments: int,
) -> list[dict]:
    """Split a fixed installment amount into interest/principal per period
    (Price/French system: interest is charged on the outstanding balance
    each period, so the principal portion grows as the balance shrinks).

    The final installment always forces the remaining balance to zero
    exactly, absorbing any rounding drift from the earlier periods.
    """
    rows: list[dict] = []
    balance = principal
    for number in range(1, num_installments + 1):
        interest = _round(balance * periodic_rate)
        if number == num_installments:
            principal_portion = balance
            amount = principal_portion + interest
        else:
            principal_portion = _round(installment_amount - interest)
            amount = installment_amount
        balance -= principal_portion
        rows.append(
            {
                "installment_number": number,
                "amount": amount,
                "principal_portion": principal_portion,
                "interest_portion": interest,
            }
        )
    return rows


async def get_debts(session: AsyncSession, workspace_id: uuid.UUID) -> list[Debt]:
    result = await session.execute(
        select(Debt)
        .where(Debt.workspace_id == workspace_id)
        .options(selectinload(Debt.plans).selectinload(DebtPlan.installments))
        .order_by(Debt.status, Debt.opened_date.desc())
    )
    return list(result.scalars().all())


async def get_debt(
    session: AsyncSession, debt_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Debt]:
    result = await session.execute(
        select(Debt)
        .where(Debt.id == debt_id, Debt.workspace_id == workspace_id)
        .options(selectinload(Debt.plans).selectinload(DebtPlan.installments))
        # A debt fetched earlier in the same session (e.g. by
        # create_debt_plan, before its new plan existed) may already have
        # `plans` cached from that stale state. Without this, selectinload
        # would leave the cached collection alone instead of refreshing it.
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def create_debt(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, data: DebtCreate
) -> Debt:
    debt = Debt(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        created_at=datetime.now(timezone.utc),
        **data.model_dump(),
    )
    session.add(debt)
    await session.commit()
    # Not session.refresh(debt): it only reloads column attributes, leaving
    # `plans` unloaded — accessing it during response serialization would
    # then trigger a synchronous lazy-load and raise MissingGreenlet. get_debt
    # re-fetches with `plans` (and their installments) eagerly populated.
    created = await get_debt(session, debt.id, workspace_id)
    assert created is not None
    return created


async def update_debt(
    session: AsyncSession, debt_id: uuid.UUID, workspace_id: uuid.UUID, data: DebtUpdate
) -> Optional[Debt]:
    debt = await get_debt(session, debt_id, workspace_id)
    if not debt:
        return None
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(debt, key, value)
    await session.commit()
    return await get_debt(session, debt_id, workspace_id)


async def delete_debt(session: AsyncSession, debt_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    debt = await get_debt(session, debt_id, workspace_id)
    if not debt:
        return False
    await session.delete(debt)
    await session.commit()
    return True


async def create_debt_plan(
    session: AsyncSession,
    debt_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: DebtPlanCreate,
) -> Optional[DebtPlan]:
    debt = await get_debt(session, debt_id, workspace_id)
    if not debt:
        return None

    if data.activate:
        # Only one active plan per debt: supersede whichever is active now.
        result = await session.execute(
            select(DebtPlan).where(DebtPlan.debt_id == debt_id, DebtPlan.status == "active")
        )
        for previous in result.scalars().all():
            previous.status = "superseded"

    payload = data.model_dump(exclude={"activate"})
    plan = DebtPlan(
        id=uuid.uuid4(),
        debt_id=debt_id,
        workspace_id=workspace_id,
        status="active" if data.activate else "proposed",
        created_at=datetime.now(timezone.utc),
        **payload,
    )
    session.add(plan)
    await session.flush()

    periodic_rate = data.interest_rate / Decimal("100")
    schedule = build_amortization_schedule(
        debt.current_balance, periodic_rate, data.installment_amount, data.num_installments
    )
    due_date = data.first_due_date
    for row in schedule:
        installment = DebtInstallment(
            id=uuid.uuid4(),
            plan_id=plan.id,
            workspace_id=workspace_id,
            due_date=due_date,
            created_at=datetime.now(timezone.utc),
            **row,
        )
        session.add(installment)
        due_date = _advance_date(due_date, data.frequency, intended_day=data.first_due_date.day)

    await session.commit()
    result = await session.execute(
        select(DebtPlan)
        .where(DebtPlan.id == plan.id)
        .options(selectinload(DebtPlan.installments))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def get_active_plan(
    session: AsyncSession, debt_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[DebtPlan]:
    result = await session.execute(
        select(DebtPlan)
        .where(
            DebtPlan.debt_id == debt_id,
            DebtPlan.workspace_id == workspace_id,
            DebtPlan.status == "active",
        )
        .options(selectinload(DebtPlan.installments))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()
