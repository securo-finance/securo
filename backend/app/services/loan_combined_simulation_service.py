"""Combined loan scenario simulation — events interplay over one timeline."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule
from app.services.loan_schedule_service import calculate_emi

TWO = Decimal("0.01")


@dataclass
class ScenarioEvent:
    type: str  # one_time_prepayment | recurring_prepayment | rate_change | emi_holiday
    date: date
    amount: Optional[Decimal] = None
    new_rate: Optional[Decimal] = None
    months: Optional[int] = None  # duration for recurring / holiday
    end_date: Optional[date] = None
    label: Optional[str] = None


@dataclass
class MonthStep:
    due_date: date
    emi_number: int
    opening: Decimal
    interest: Decimal
    principal: Decimal
    extra_principal: Decimal
    closing: Decimal
    rate: Decimal
    emi: Decimal
    events: list[str] = field(default_factory=list)
    holiday: bool = False


def _q(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


def _expand_events(events: list[ScenarioEvent], horizon_end: date) -> dict[date, list[ScenarioEvent]]:
    """Map calendar dates → concrete event instances (expand recurring)."""
    by_date: dict[date, list[ScenarioEvent]] = {}
    for ev in events:
        if ev.type == "recurring_prepayment":
            if not ev.amount or ev.amount <= 0:
                continue
            end = ev.end_date
            if end is None and ev.months:
                end = ev.date + relativedelta(months=ev.months)
            if end is None:
                end = horizon_end
            cursor = ev.date
            while cursor <= end and cursor <= horizon_end:
                by_date.setdefault(cursor, []).append(
                    ScenarioEvent(
                        type="one_time_prepayment",
                        date=cursor,
                        amount=ev.amount,
                        label=ev.label or f"Recurring +{ev.amount}",
                    )
                )
                cursor = cursor + relativedelta(months=1)
        elif ev.type == "emi_holiday":
            months = ev.months or 1
            for i in range(months):
                d = ev.date + relativedelta(months=i)
                by_date.setdefault(d, []).append(
                    ScenarioEvent(type="emi_holiday", date=d, label=ev.label or "EMI holiday")
                )
        else:
            by_date.setdefault(ev.date, []).append(ev)
    return by_date


def walk_amortization(
    *,
    principal: Decimal,
    annual_rate: Decimal,
    emi: Decimal,
    start_date: date,
    emi_day: int,
    events: list[ScenarioEvent],
    strategy: str = "reduce_tenure",
    max_months: int = 600,
) -> list[MonthStep]:
    """Month-by-month walk applying combined events.

    strategy:
      - reduce_tenure: keep EMI, apply extras to principal (pay off sooner)
      - reduce_emi: after each extra / rate change, recalc EMI for remaining est. tenure
    """
    if principal <= 0:
        return []

    # Expand with a generous horizon for recurring
    horizon = start_date + relativedelta(months=max_months)
    by_date = _expand_events(events, horizon)

    rate = annual_rate
    balance = principal
    steps: list[MonthStep] = []
    # Estimate remaining months for reduce_emi recalcs
    remaining_est = max_months

    for i in range(1, max_months + 1):
        due = start_date + relativedelta(months=i)
        try:
            due = due.replace(day=emi_day)
        except ValueError:
            due = due + relativedelta(day=31)

        month_events = by_date.get(due, [])
        # Also pick events between previous due and this due (one-time mid-cycle)
        prev = steps[-1].due_date if steps else start_date
        for d, evs in list(by_date.items()):
            if prev < d < due:
                month_events = evs + month_events

        labels: list[str] = []
        holiday = False
        extra = Decimal("0.00")

        for ev in month_events:
            if ev.type == "rate_change" and ev.new_rate is not None:
                rate = ev.new_rate
                labels.append(ev.label or f"Rate → {rate}%")
                if strategy == "reduce_emi" and remaining_est > 0 and balance > 0:
                    emi = calculate_emi(balance, rate, remaining_est)
            elif ev.type == "emi_holiday":
                holiday = True
                labels.append(ev.label or "EMI holiday")
            elif ev.type == "one_time_prepayment" and ev.amount:
                extra += ev.amount
                labels.append(ev.label or f"Prepay {ev.amount}")

        monthly_rate = rate / Decimal("1200") if rate > 0 else Decimal("0")
        opening = balance
        interest = _q(opening * monthly_rate)

        if holiday:
            principal_comp = Decimal("0.00")
            payment_emi = Decimal("0.00")
            # Accrue interest onto balance
            balance = opening + interest
            # Still allow prepayments during holiday
            extra_applied = min(extra, balance)
            balance = _q(balance - extra_applied)
            closing = balance
            steps.append(
                MonthStep(
                    due_date=due,
                    emi_number=i,
                    opening=opening,
                    interest=interest,
                    principal=principal_comp,
                    extra_principal=extra_applied,
                    closing=closing,
                    rate=rate,
                    emi=payment_emi,
                    events=labels,
                    holiday=True,
                )
            )
        else:
            # Standard EMI
            if monthly_rate > 0 and emi <= interest and extra <= 0:
                # EMI can't cover interest — bail
                break
            principal_comp = _q(emi - interest) if emi > interest else Decimal("0.00")
            if principal_comp > opening:
                principal_comp = opening
                interest = _q(emi - principal_comp) if emi >= principal_comp else interest
            # Apply EMI principal then extras
            balance = _q(opening - principal_comp)
            extra_applied = min(extra, balance)
            balance = _q(balance - extra_applied)
            closing = max(balance, Decimal("0.00"))
            payment_emi = emi
            steps.append(
                MonthStep(
                    due_date=due,
                    emi_number=i,
                    opening=opening,
                    interest=interest,
                    principal=principal_comp,
                    extra_principal=extra_applied,
                    closing=closing,
                    rate=rate,
                    emi=payment_emi,
                    events=labels,
                    holiday=False,
                )
            )
            if strategy == "reduce_emi" and extra_applied > 0 and closing > 0:
                # Recalc EMI for remaining estimate
                remaining_est = max(1, remaining_est - 1)
                emi = calculate_emi(closing, rate, remaining_est)

        remaining_est = max(1, remaining_est - 1)
        if closing <= Decimal("0.00"):
            break

    return steps


def summarize(steps: list[MonthStep], emi: Decimal, rate: Decimal) -> dict[str, Any]:
    total_interest = sum((s.interest for s in steps), Decimal("0"))
    total_principal = sum((s.principal + s.extra_principal for s in steps), Decimal("0"))
    payoff = steps[-1].due_date if steps else None
    return {
        "months": len(steps),
        "payoff_date": payoff.isoformat() if payoff else None,
        "total_interest": float(_q(total_interest)),
        "total_principal_paid": float(_q(total_principal)),
        "emi": float(emi),
        "ending_rate": float(steps[-1].rate) if steps else float(rate),
        "final_balance": float(steps[-1].closing) if steps else 0.0,
    }


def steps_to_timeline(steps: list[MonthStep], max_rows: int = 480) -> list[dict]:
    out = []
    for s in steps[:max_rows]:
        out.append(
            {
                "due_date": s.due_date.isoformat(),
                "emi_number": s.emi_number,
                "opening_balance": float(s.opening),
                "interest": float(s.interest),
                "principal": float(s.principal),
                "extra_principal": float(s.extra_principal),
                "closing_balance": float(s.closing),
                "rate": float(s.rate),
                "emi": float(s.emi),
                "events": s.events,
                "holiday": s.holiday,
            }
        )
    return out


def steps_to_curves(steps: list[MonthStep]) -> dict[str, list]:
    dates, outstanding, cum_int, cum_prin = [], [], [], []
    ti = Decimal("0")
    tp = Decimal("0")
    for s in steps:
        ti += s.interest
        tp += s.principal + s.extra_principal
        dates.append(s.due_date.isoformat())
        outstanding.append(float(s.closing))
        cum_int.append(float(_q(ti)))
        cum_prin.append(float(_q(tp)))
    return {
        "dates": dates,
        "outstanding": outstanding,
        "cumulative_interest": cum_int,
        "cumulative_principal": cum_prin,
    }


async def _loan_starting_point(
    session: AsyncSession, account_id: uuid.UUID, as_of: date
) -> tuple[Account, Decimal, Decimal, Decimal, date, int]:
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account or account.type != "loan":
        raise ValueError("Loan account not found")

    sched = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.payment_status == "scheduled",
        )
        .order_by(LoanAmortizationSchedule.emi_number)
        .limit(1)
    )
    next_entry = sched.scalar_one_or_none()
    if next_entry:
        principal = next_entry.opening_balance
        emi = next_entry.emi_amount or account.emi_amount
        start_anchor = next_entry.due_date - relativedelta(months=1)
    else:
        principal = account.balance or account.original_principal or Decimal("0")
        emi = account.emi_amount
        start_anchor = as_of
    if not emi:
        tenure = account.tenure_months or 12
        emi = calculate_emi(principal, account.interest_rate or Decimal("0"), tenure)
    rate = account.interest_rate or Decimal("0")
    emi_day = account.emi_day or (account.disbursed_on.day if account.disbursed_on else 1)
    return account, principal, rate, emi, start_anchor, emi_day


def parse_events(raw: list[dict]) -> list[ScenarioEvent]:
    events: list[ScenarioEvent] = []
    for item in raw:
        events.append(
            ScenarioEvent(
                type=str(item["type"]),
                date=date.fromisoformat(str(item["date"])),
                amount=Decimal(str(item["amount"])) if item.get("amount") is not None else None,
                new_rate=Decimal(str(item["new_rate"])) if item.get("new_rate") is not None else None,
                months=int(item["months"]) if item.get("months") is not None else None,
                end_date=date.fromisoformat(str(item["end_date"])) if item.get("end_date") else None,
                label=item.get("label"),
            )
        )
    return events


async def simulate_combined(
    session: AsyncSession,
    account_id: uuid.UUID,
    events: list[dict],
    strategy: str = "reduce_tenure",
    as_of_date: Optional[date] = None,
) -> dict:
    as_of = as_of_date or date.today()
    account, principal, rate, emi, start_anchor, emi_day = await _loan_starting_point(
        session, account_id, as_of
    )
    parsed = parse_events(events)

    baseline = walk_amortization(
        principal=principal,
        annual_rate=rate,
        emi=emi,
        start_date=start_anchor,
        emi_day=emi_day,
        events=[],
        strategy="reduce_tenure",
    )
    scenario = walk_amortization(
        principal=principal,
        annual_rate=rate,
        emi=emi,
        start_date=start_anchor,
        emi_day=emi_day,
        events=parsed,
        strategy=strategy,
    )

    base_sum = summarize(baseline, emi, rate)
    scen_sum = summarize(scenario, emi, rate)
    months_saved = base_sum["months"] - scen_sum["months"]
    interest_saved = _q(Decimal(str(base_sum["total_interest"])) - Decimal(str(scen_sum["total_interest"])))

    return {
        "account_id": str(account_id),
        "as_of": as_of.isoformat(),
        "strategy": strategy,
        "starting_principal": float(principal),
        "starting_rate": float(rate),
        "starting_emi": float(emi),
        "events": events,
        "baseline": base_sum,
        "scenario": scen_sum,
        "kpis": {
            "total_interest": scen_sum["total_interest"],
            "baseline_interest": base_sum["total_interest"],
            "interest_saved": float(interest_saved),
            "months_saved": months_saved,
            "emi": float(emi),
            "payoff_date": scen_sum["payoff_date"],
            "baseline_payoff_date": base_sum["payoff_date"],
            "scenario_months": scen_sum["months"],
            "baseline_months": base_sum["months"],
        },
        "timeline": steps_to_timeline(scenario),
        "curves": steps_to_curves(scenario),
        "baseline_curves": steps_to_curves(baseline),
    }
