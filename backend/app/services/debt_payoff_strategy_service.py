"""Snowball/avalanche payoff projection across a workspace's active debts.

Purely computed on demand from `Debt` + its active `DebtPlan` — nothing
here is persisted (only the *choice* of method/extra payment lives in
`DebtStrategySetting`). Projection advances in monthly steps regardless
of a plan's own frequency; mixed-frequency portfolios get an
approximate timeline rather than an exact one, which is an acceptable
trade-off for a "how many months until I'm debt-free" projection.

Extra monthly payment is only ever applied to `manual`-collection debts.
A `payroll_deduction` installment is fixed by the employer's payroll
cycle regardless of strategy, so it advances on its own schedule and
never receives the snowball/avalanche extra.
"""
import uuid
from datetime import date as _date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.debt import Debt, DebtPlan, DebtStrategySetting
from app.schemas.debt import (
    DebtPayoffProjection,
    DebtPayoffProjectionEntry,
    DebtStrategySettingUpdate,
)
from app.services.recurring_transaction_service import _advance_date

MAX_MONTHS = 1200  # 100 years — a hard stop against a misconfigured plan looping forever


async def get_or_create_strategy_setting(
    session: AsyncSession, workspace_id: uuid.UUID
) -> DebtStrategySetting:
    result = await session.execute(
        select(DebtStrategySetting).where(DebtStrategySetting.workspace_id == workspace_id)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = DebtStrategySetting(id=uuid.uuid4(), workspace_id=workspace_id)
        session.add(setting)
        await session.commit()
        await session.refresh(setting)
    return setting


async def update_strategy_setting(
    session: AsyncSession, workspace_id: uuid.UUID, data: DebtStrategySettingUpdate
) -> DebtStrategySetting:
    setting = await get_or_create_strategy_setting(session, workspace_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(setting, key, value)
    await session.commit()
    await session.refresh(setting)
    return setting


class _SimDebt:
    __slots__ = (
        "debt_id",
        "creditor_name",
        "balance",
        "periodic_rate",
        "installment_amount",
        "collection_mode",
        "months_to_payoff",
        "interest_paid",
    )

    def __init__(self, debt: Debt, plan: DebtPlan):
        self.debt_id = debt.id
        self.creditor_name = debt.creditor_name
        self.balance = debt.current_balance
        self.periodic_rate = plan.interest_rate / Decimal("100")
        self.installment_amount = plan.installment_amount
        self.collection_mode = plan.collection_mode
        self.months_to_payoff: Optional[int] = None
        self.interest_paid = Decimal("0")


async def _active_sim_debts(session: AsyncSession, workspace_id: uuid.UUID) -> list[_SimDebt]:
    result = await session.execute(
        select(Debt)
        .where(Debt.workspace_id == workspace_id, Debt.status == "active")
        .options(selectinload(Debt.plans))
        .execution_options(populate_existing=True)
    )
    sims: list[_SimDebt] = []
    for debt in result.scalars().all():
        active_plan = next((p for p in debt.plans if p.status == "active"), None)
        if active_plan is None or debt.current_balance <= 0:
            continue
        sims.append(_SimDebt(debt, active_plan))
    return sims


def _order(sims: list[_SimDebt], method: str) -> list[_SimDebt]:
    if method == "snowball":
        return sorted(sims, key=lambda s: s.balance)
    return sorted(sims, key=lambda s: s.periodic_rate, reverse=True)


async def compute_payoff_projection(
    session: AsyncSession, workspace_id: uuid.UUID
) -> DebtPayoffProjection:
    setting = await get_or_create_strategy_setting(session, workspace_id)
    sims = await _active_sim_debts(session, workspace_id)
    ordered = _order(sims, setting.method)

    month = 0
    while any(s.balance > 0 for s in ordered) and month < MAX_MONTHS:
        month += 1
        extra_available = setting.extra_monthly_amount
        for sim in ordered:
            if sim.balance <= 0:
                continue
            payment = sim.installment_amount
            if sim.collection_mode == "manual" and extra_available > 0:
                payment += extra_available
                extra_available = Decimal("0")
            interest = sim.balance * sim.periodic_rate
            principal_pay = min(payment - interest, sim.balance)
            if principal_pay < 0:
                principal_pay = Decimal("0")
            sim.balance -= principal_pay
            sim.interest_paid += interest
            if sim.balance <= 0 and sim.months_to_payoff is None:
                sim.balance = Decimal("0")
                sim.months_to_payoff = month

    today = _date.today()
    entries = [
        DebtPayoffProjectionEntry(
            debt_id=sim.debt_id,
            creditor_name=sim.creditor_name,
            months_to_payoff=sim.months_to_payoff,
            payoff_date=(
                _advance_date_months(today, sim.months_to_payoff) if sim.months_to_payoff else None
            ),
            total_interest_remaining=sim.interest_paid.quantize(Decimal("0.01")),
        )
        for sim in ordered
    ]
    finished = [s.months_to_payoff for s in ordered if s.months_to_payoff is not None]
    overall_months = max(finished) if finished and len(finished) == len(ordered) else None
    return DebtPayoffProjection(
        method=setting.method,
        extra_monthly_amount=setting.extra_monthly_amount,
        order=entries,
        overall_payoff_date=_advance_date_months(today, overall_months) if overall_months else None,
    )


def _advance_date_months(start: _date, months: int) -> _date:
    current = start
    for _ in range(months):
        current = _advance_date(current, "monthly", intended_day=start.day)
    return current
