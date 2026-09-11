"""Loan simulation service for various scenarios."""
import math
import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule


def calculate_emi(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    """Calculate EMI using the standard formula."""
    if months <= 0 or principal <= 0:
        return Decimal("0.00")

    if annual_rate == 0:
        return (principal / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    monthly_rate = annual_rate / Decimal("1200")
    r = float(monthly_rate)
    n = months
    p = float(principal)

    emi = p * r * math.pow(1 + r, n) / (math.pow(1 + r, n) - 1)
    return Decimal(str(emi)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def simulate_early_payment(
    session: AsyncSession,
    account_id: uuid.UUID,
    prepayment_amount: Decimal,
    prepayment_date: Optional[date] = None,
) -> dict:
    """Simulate early payment with reduce EMI vs reduce tenure options."""
    if prepayment_date is None:
        prepayment_date = date.today()

    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise ValueError("Account not found")

    # Get current schedule
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.payment_status == "scheduled",
        )
        .order_by(LoanAmortizationSchedule.emi_number)
    )
    future_entries = list(schedule_result.scalars().all())

    if not future_entries:
        return {
            "error": "No future EMIs found",
            "reduce_emi": None,
            "reduce_tenure": None,
        }

    # Current outstanding balance
    current_outstanding = future_entries[0].opening_balance
    new_principal = current_outstanding - prepayment_amount

    if new_principal < 0:
        new_principal = Decimal("0")

    remaining_months = len(future_entries)
    current_emi = account.emi_amount or Decimal("0")
    monthly_rate = (account.interest_rate or Decimal("0")) / Decimal("1200")

    # Option 1: Reduce EMI (keep same tenure)
    new_emi = calculate_emi(new_principal, account.interest_rate or Decimal("0"), remaining_months)
    emi_reduction = current_emi - new_emi

    # Calculate interest saved for reduce EMI option
    old_interest_emi = sum(e.interest_component for e in future_entries)
    new_interest_emi = Decimal("0")
    balance = new_principal
    for _ in range(remaining_months):
        interest = (balance * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal = new_emi - interest
        new_interest_emi += interest
        balance -= principal
        if balance <= 0:
            break
    interest_saved_emi = old_interest_emi - new_interest_emi

    # Option 2: Reduce Tenure (keep same EMI)
    new_tenure_months = 0
    months_saved = 0
    interest_saved_tenure = Decimal("0")
    new_payoff_date = None

    if current_emi > 0 and monthly_rate > 0:
        # Calculate new tenure
        r_float = float(monthly_rate)
        emi_float = float(current_emi)
        p_float = float(new_principal)

        if emi_float > p_float * r_float:
            n_float = math.log(emi_float / (emi_float - p_float * r_float)) / math.log(1 + r_float)
            new_tenure_months = int(math.ceil(n_float))
        else:
            new_tenure_months = int((new_principal / current_emi).to_integral_value(rounding=ROUND_HALF_UP))

        months_saved = remaining_months - new_tenure_months

        # Calculate new payoff date
        if future_entries:
            new_payoff_date = future_entries[0].due_date + timedelta(days=30 * new_tenure_months)

        # Calculate interest saved for reduce tenure
        old_interest_tenure = sum(e.interest_component for e in future_entries)
        new_interest_tenure = Decimal("0")
        balance = new_principal
        for _ in range(new_tenure_months):
            interest = (balance * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            principal = current_emi - interest
            new_interest_tenure += interest
            balance -= principal
            if balance <= 0:
                break
        interest_saved_tenure = old_interest_tenure - new_interest_tenure

    return {
        "current_state": {
            "outstanding_balance": float(current_outstanding),
            "current_emi": float(current_emi),
            "remaining_months": remaining_months,
            "current_interest_rate": float(account.interest_rate or 0),
        },
        "prepayment": {
            "amount": float(prepayment_amount),
            "date": prepayment_date.isoformat(),
            "new_principal": float(new_principal),
        },
        "reduce_emi": {
            "new_emi": float(new_emi),
            "emi_reduction": float(emi_reduction),
            "emi_reduction_percent": float((emi_reduction / current_emi * 100) if current_emi > 0 else 0),
            "tenure_months": remaining_months,
            "interest_saved": float(interest_saved_emi),
            "total_savings": float(interest_saved_emi),
        },
        "reduce_tenure": {
            "emi": float(current_emi),
            "new_tenure_months": new_tenure_months,
            "months_saved": months_saved,
            "new_payoff_date": new_payoff_date.isoformat() if new_payoff_date else None,
            "interest_saved": float(interest_saved_tenure),
            "total_savings": float(interest_saved_tenure),
        },
    }


async def simulate_preclosure(
    session: AsyncSession,
    account_id: uuid.UUID,
    closure_date: Optional[date] = None,
) -> dict:
    """Simulate loan pre-closure and calculate payoff amount."""
    if closure_date is None:
        closure_date = date.today()

    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise ValueError("Account not found")

    # Get all schedule entries
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
        )
        .order_by(LoanAmortizationSchedule.emi_number)
    )
    all_entries = list(schedule_result.scalars().all())

    # Calculate paid amounts
    paid_entries = [e for e in all_entries if e.payment_status == "paid"]
    scheduled_entries = [e for e in all_entries if e.payment_status == "scheduled"]

    principal_paid = sum(e.principal_component for e in paid_entries)
    interest_paid = sum(e.interest_component for e in paid_entries)

    # Outstanding principal
    outstanding_principal = account.balance or Decimal("0")

    # Calculate accrued interest till closure date
    if scheduled_entries:
        next_emi = scheduled_entries[0]
        anchor = paid_entries[-1].actual_payment_date if paid_entries else (account.disbursed_on or closure_date)
        days_since_last_payment = (closure_date - anchor).days

        daily_rate = (account.interest_rate or Decimal("0")) / Decimal("36500")
        accrued_interest = (outstanding_principal * daily_rate * days_since_last_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        accrued_interest = Decimal("0")

    # Prepayment penalty (typically 2-3% of outstanding, adjust based on your rules)
    prepayment_penalty_rate = Decimal("2.0")  # 2%
    prepayment_penalty = (outstanding_principal * prepayment_penalty_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Total payoff amount
    payoff_amount = outstanding_principal + accrued_interest + prepayment_penalty

    # Interest saved (remaining interest that won't be paid)
    interest_saved = sum(e.interest_component for e in scheduled_entries)

    # Net savings (interest saved - penalty)
    net_savings = interest_saved - prepayment_penalty

    return {
        "closure_date": closure_date.isoformat(),
        "outstanding_principal": float(outstanding_principal),
        "accrued_interest": float(accrued_interest),
        "prepayment_penalty": float(prepayment_penalty),
        "prepayment_penalty_rate": float(prepayment_penalty_rate),
        "total_payoff_amount": float(payoff_amount),
        "interest_saved": float(interest_saved),
        "net_savings": float(net_savings),
        "paid_to_date": {
            "principal": float(principal_paid),
            "interest": float(interest_paid),
            "total": float(principal_paid + interest_paid),
        },
        "remaining_emis": len(scheduled_entries),
    }


async def simulate_interest_rate_change(
    session: AsyncSession,
    account_id: uuid.UUID,
    new_interest_rate: Decimal,
    effective_from_date: Optional[date] = None,
) -> dict:
    """Simulate impact of interest rate change."""
    if effective_from_date is None:
        effective_from_date = date.today()

    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise ValueError("Account not found")

    # Get future schedule
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.payment_status == "scheduled",
        )
        .order_by(LoanAmortizationSchedule.emi_number)
    )
    future_entries = list(schedule_result.scalars().all())

    if not future_entries:
        return {"error": "No future EMIs found"}

    current_outstanding = future_entries[0].opening_balance
    remaining_months = len(future_entries)
    current_rate = account.interest_rate or Decimal("0")
    current_emi = account.emi_amount or Decimal("0")

    # Calculate new EMI with new rate
    new_emi = calculate_emi(current_outstanding, new_interest_rate, remaining_months)
    emi_change = new_emi - current_emi
    emi_change_percent = (emi_change / current_emi * 100) if current_emi > 0 else Decimal("0")

    # Calculate interest difference
    old_total_interest = sum(e.interest_component for e in future_entries)

    new_monthly_rate = new_interest_rate / Decimal("1200")
    new_total_interest = Decimal("0")
    balance = current_outstanding
    for _ in range(remaining_months):
        interest = (balance * new_monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal = new_emi - interest
        new_total_interest += interest
        balance -= principal
        if balance <= 0:
            break

    interest_difference = new_total_interest - old_total_interest

    return {
        "current_rate": float(current_rate),
        "new_rate": float(new_interest_rate),
        "rate_change": float(new_interest_rate - current_rate),
        "effective_from": effective_from_date.isoformat(),
        "current_emi": float(current_emi),
        "new_emi": float(new_emi),
        "emi_change": float(emi_change),
        "emi_change_percent": float(emi_change_percent),
        "outstanding_balance": float(current_outstanding),
        "remaining_months": remaining_months,
        "current_total_interest": float(old_total_interest),
        "new_total_interest": float(new_total_interest),
        "interest_difference": float(interest_difference),
        "is_favorable": interest_difference < 0,
    }



async def apply_preclosure(
    session: AsyncSession,
    account_id: uuid.UUID,
    closure_date: Optional[date] = None,
    create_payoff_transaction: bool = False,
) -> dict:
    """Close a loan using the preclosure quote: mark remaining EMIs skipped, zero balance."""
    quote = await simulate_preclosure(session, account_id, closure_date)
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()
    if closure_date is None:
        closure_date = date.today()

    schedule_result = await session.execute(
        select(LoanAmortizationSchedule).where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.payment_status == "scheduled",
        )
    )
    for entry in schedule_result.scalars().all():
        entry.payment_status = "skipped"
        entry.notes = (entry.notes or "") + f" | Pre-closed {closure_date.isoformat()}"

    account.balance = Decimal("0.00")
    account.is_closed = True
    from datetime import datetime, timezone
    account.closed_at = datetime.now(timezone.utc)
    account.last_payment_date = closure_date
    await session.commit()
    return {"closed": True, "account_id": str(account_id), "quote": quote}


async def apply_interest_rate_change(
    session: AsyncSession,
    account_id: uuid.UUID,
    new_interest_rate: Decimal,
    effective_from_date: Optional[date] = None,
    strategy: str = "keep_tenure",
) -> dict:
    """Apply a new interest rate and regenerate remaining schedule.

    strategy:
      - keep_tenure: recalculate EMI for remaining months
      - keep_emi: recalculate tenure for current EMI (approx via regenerate with same EMI)
    """
    from app.services.loan_schedule_service import regenerate_schedule, calculate_emi

    sim = await simulate_interest_rate_change(session, account_id, new_interest_rate, effective_from_date)
    if sim.get("error"):
        raise ValueError(sim["error"])

    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.payment_status == "scheduled",
        )
        .order_by(LoanAmortizationSchedule.emi_number)
        .limit(1)
    )
    next_entry = schedule_result.scalar_one_or_none()
    if not next_entry:
        raise ValueError("No future EMIs to regenerate")

    remaining_months = int(sim["remaining_months"])
    outstanding = Decimal(str(sim["outstanding_balance"]))
    old_rate = account.interest_rate
    account.interest_rate = new_interest_rate

    if strategy == "keep_emi" and account.emi_amount:
        new_params = {
            "new_principal_balance": outstanding,
            "new_interest_rate": new_interest_rate,
            "new_emi_amount": account.emi_amount,
            "new_tenure_months": None,  # regenerate will derive from EMI math if we pass tenure
        }
        # Derive tenure from EMI formula
        monthly_rate = new_interest_rate / Decimal("1200") if new_interest_rate > 0 else Decimal("0")
        if monthly_rate > 0:
            import math
            r = float(monthly_rate)
            emi = float(account.emi_amount)
            p = float(outstanding)
            if emi > p * r:
                tenure = int(math.ceil(math.log(emi / (emi - p * r)) / math.log(1 + r)))
            else:
                tenure = remaining_months
        else:
            tenure = int((outstanding / account.emi_amount).to_integral_value())
        new_params["new_tenure_months"] = max(1, tenure)
    else:
        new_emi = calculate_emi(outstanding, new_interest_rate, remaining_months)
        account.emi_amount = new_emi
        new_params = {
            "new_principal_balance": outstanding,
            "new_interest_rate": new_interest_rate,
            "new_tenure_months": remaining_months,
            "new_emi_amount": new_emi,
        }

    entries = await regenerate_schedule(session, account_id, next_entry.emi_number, new_params)
    await session.refresh(account)
    return {
        "applied": True,
        "old_rate": float(old_rate or 0),
        "new_rate": float(new_interest_rate),
        "strategy": strategy,
        "simulation": sim,
        "new_schedule_version": account.current_schedule_version,
        "entries_created": len(entries),
    }
