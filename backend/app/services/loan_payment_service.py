import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule
from app.models.loan_prepayment import LoanPrepayment
from app.models.transaction import Transaction
from app.services.loan_schedule_service import calculate_emi, regenerate_schedule


async def auto_link_transactions(
    session: AsyncSession,
    account_id: uuid.UUID,
    date_tolerance_days: int = 5,
    amount_tolerance_pct: Decimal = Decimal("2.0"),
) -> tuple[list[dict], int, int]:
    """Auto-link transactions to schedule entries based on date and amount.

    Returns:
        tuple: (potential_matches, auto_linked_count, requires_review_count)
    """
    # Fetch current schedule version
    result = await session.execute(select(Account.current_schedule_version).where(Account.id == account_id))
    version = result.scalar_one()

    # Get unlinked schedule entries with status "scheduled"
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule).where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == version,
            LoanAmortizationSchedule.payment_status == "scheduled",
            LoanAmortizationSchedule.linked_transaction_id.is_(None),
        )
    )
    entries = list(schedule_result.scalars().all())

    # Get unlinked transactions for this account
    tx_result = await session.execute(
        select(Transaction).where(Transaction.account_id == account_id, Transaction.type == "debit")
    )
    transactions = list(tx_result.scalars().all())

    # Filter out already linked transactions
    linked_tx_result = await session.execute(
        select(LoanAmortizationSchedule.linked_transaction_id).where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.linked_transaction_id.is_not(None),
        )
    )
    linked_tx_ids = {row[0] for row in linked_tx_result.all()}
    transactions = [tx for tx in transactions if tx.id not in linked_tx_ids]

    matches = []
    auto_linked = 0
    requires_review = 0

    for entry in entries:
        for tx in transactions:
            # Check date tolerance
            date_diff = abs((tx.date - entry.due_date).days)
            if date_diff > date_tolerance_days:
                continue

            # Check amount tolerance
            amount_diff_pct = abs((tx.amount - entry.emi_amount) / entry.emi_amount * 100)
            if amount_diff_pct > amount_tolerance_pct:
                continue

            # Determine confidence
            if date_diff == 0 and amount_diff_pct < Decimal("0.01"):
                confidence = "exact"
            elif date_diff <= 2 and amount_diff_pct < Decimal("1.0"):
                confidence = "high"
            elif date_diff <= 5 and amount_diff_pct < Decimal("2.0"):
                confidence = "medium"
            else:
                confidence = "low"

            match = {
                "schedule_entry_id": entry.id,
                "transaction_id": tx.id,
                "due_date": entry.due_date,
                "transaction_date": tx.date,
                "emi_amount": float(entry.emi_amount),
                "transaction_amount": float(tx.amount),
                "confidence": confidence,
            }
            matches.append(match)

            # Auto-link high confidence matches
            if confidence in ["exact", "high"]:
                entry.linked_transaction_id = tx.id
                entry.payment_status = "paid"
                entry.actual_payment_date = tx.date
                entry.actual_amount_paid = tx.amount
                auto_linked += 1
            else:
                requires_review += 1

    await session.commit()
    return matches, auto_linked, requires_review


async def link_transaction_to_entry(
    session: AsyncSession, entry_id: uuid.UUID, transaction_id: uuid.UUID
) -> LoanAmortizationSchedule:
    """Manually link a transaction to a schedule entry."""
    entry_result = await session.execute(select(LoanAmortizationSchedule).where(LoanAmortizationSchedule.id == entry_id))
    entry = entry_result.scalar_one()

    tx_result = await session.execute(select(Transaction).where(Transaction.id == transaction_id))
    tx = tx_result.scalar_one()

    entry.linked_transaction_id = transaction_id
    entry.payment_status = "paid"
    entry.actual_payment_date = tx.date
    entry.actual_amount_paid = tx.amount

    await session.commit()
    await session.refresh(entry)
    return entry


async def mark_payment_status(
    session: AsyncSession,
    entry_id: uuid.UUID,
    status: str,
    actual_date: Optional[date] = None,
    actual_amount: Optional[Decimal] = None,
) -> LoanAmortizationSchedule:
    """Mark a schedule entry's payment status."""
    result = await session.execute(select(LoanAmortizationSchedule).where(LoanAmortizationSchedule.id == entry_id))
    entry = result.scalar_one()

    entry.payment_status = status
    if actual_date:
        entry.actual_payment_date = actual_date
    if actual_amount:
        entry.actual_amount_paid = actual_amount

    await session.commit()
    await session.refresh(entry)
    return entry


async def simulate_prepayment(
    session: AsyncSession, account_id: uuid.UUID, prepayment_amount: Decimal, prepayment_date: date
) -> dict:
    """Simulate both prepayment recalculation options without applying changes."""
    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    from app.services.loan_schedule_service import calculate_emi as _calc_emi

    # Get current schedule to determine position
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
        )
        .order_by(LoanAmortizationSchedule.emi_number)
    )
    entries = list(schedule_result.scalars().all())

    if account.emi_amount is not None:
        effective_emi = account.emi_amount
    elif entries:
        effective_emi = entries[0].emi_amount
    else:
        effective_emi = _calc_emi(
            account.original_principal or Decimal("0"),
            account.interest_rate or Decimal("0"),
            account.tenure_months or 1,
        )

    # Find next EMI after prepayment date
    next_emi_idx = next((i for i, e in enumerate(entries) if e.due_date > prepayment_date), len(entries))
    if next_emi_idx == 0:
        next_emi_idx = 1  # prepayment before first EMI

    # Calculate current outstanding (from next EMI's opening balance)
    if next_emi_idx < len(entries):
        current_outstanding = entries[next_emi_idx].opening_balance
    else:
        current_outstanding = Decimal("0.00")

    new_principal = current_outstanding - prepayment_amount
    remaining_months = len(entries) - next_emi_idx

    # Option 1: Reduce EMI
    if remaining_months > 0:
        new_emi_reduce_emi = calculate_emi(new_principal, account.interest_rate, remaining_months)
        emi_reduction = effective_emi - new_emi_reduce_emi

        # Calculate interest saved (simplified: compare total interest)
        old_total_interest = sum(e.interest_component for e in entries[next_emi_idx:])
        monthly_rate = account.interest_rate / Decimal("1200") if account.interest_rate > 0 else Decimal("0")
        new_total_interest = Decimal("0")
        remaining = new_principal
        for _ in range(remaining_months):
            interest = (remaining * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            principal = new_emi_reduce_emi - interest
            new_total_interest += interest
            remaining -= principal
        interest_saved_emi = old_total_interest - new_total_interest
    else:
        new_emi_reduce_emi = Decimal("0.00")
        emi_reduction = Decimal("0.00")
        interest_saved_emi = Decimal("0.00")

    # Option 2: Reduce Tenure
    if effective_emi > 0:
        # Calculate new tenure using EMI formula rearranged
        monthly_rate = account.interest_rate / Decimal("1200") if account.interest_rate > 0 else Decimal("0")
        if monthly_rate > 0:
            # n = log(EMI / (EMI - P*r)) / log(1 + r)
            import math
            r_float = float(monthly_rate)
            emi_float = float(effective_emi)
            p_float = float(new_principal)
            if emi_float > p_float * r_float:
                n_float = math.log(emi_float / (emi_float - p_float * r_float)) / math.log(1 + r_float)
                new_tenure = int(math.ceil(n_float))
            else:
                new_tenure = remaining_months
        else:
            new_tenure = int((new_principal / effective_emi).to_integral_value(rounding=ROUND_HALF_UP))

        months_saved = remaining_months - new_tenure
        new_payoff_date = entries[next_emi_idx].due_date + timedelta(days=30 * new_tenure) if next_emi_idx < len(entries) else prepayment_date

        # Calculate interest saved
        old_total_interest = sum(e.interest_component for e in entries[next_emi_idx:])
        new_total_interest = Decimal("0")
        remaining = new_principal
        for _ in range(new_tenure):
            interest = (remaining * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            principal = effective_emi - interest
            new_total_interest += interest
            remaining -= principal
        interest_saved_tenure = old_total_interest - new_total_interest
    else:
        new_tenure = 0
        months_saved = 0
        new_payoff_date = prepayment_date
        interest_saved_tenure = Decimal("0.00")

    reduce_emi_option = {
        "new_emi_amount": new_emi_reduce_emi,
        "emi_reduction": emi_reduction,
        "tenure_months": remaining_months,
        "total_interest_saved": interest_saved_emi,
        "sample_schedule": [],
    }
    reduce_tenure_option = {
        "new_tenure_months": new_tenure,
        "months_saved": months_saved,
        "new_payoff_date": new_payoff_date,
        "emi_amount": effective_emi,
        "total_interest_saved": interest_saved_tenure,
        "sample_schedule": [],
    }
    # Aliases expected by older route tests / some FE drafts
    return {
        "reduce_emi_option": reduce_emi_option,
        "reduce_tenure_option": reduce_tenure_option,
        "reduce_emi": reduce_emi_option,
        "reduce_tenure": reduce_tenure_option,
    }


async def record_prepayment(
    session: AsyncSession,
    account_id: uuid.UUID,
    prepayment_amount: Decimal,
    prepayment_date: date,
    method: str,
    transaction_id: Optional[uuid.UUID] = None,
) -> LoanPrepayment:
    """Record a prepayment and regenerate schedule."""
    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Get simulation to determine new parameters
    simulation = await simulate_prepayment(session, account_id, prepayment_amount, prepayment_date)

    # Determine next EMI number
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
            LoanAmortizationSchedule.due_date > prepayment_date,
        )
        .order_by(LoanAmortizationSchedule.emi_number)
        .limit(1)
    )
    next_entry = schedule_result.scalar_one_or_none()
    from_emi_number = next_entry.emi_number if next_entry else 1

    # Calculate new principal
    current_outstanding = next_entry.opening_balance if next_entry else account.balance
    new_principal = current_outstanding - prepayment_amount

    old_version = account.current_schedule_version

    # Regenerate schedule based on method
    if method == "reduce_emi":
        remaining_months = simulation["reduce_emi_option"]["tenure_months"]
        new_params = {
            "new_principal_balance": new_principal,
            "new_interest_rate": account.interest_rate,
            "new_tenure_months": remaining_months,
            "new_emi_amount": simulation["reduce_emi_option"]["new_emi_amount"],
        }
        await regenerate_schedule(session, account_id, from_emi_number, new_params)
        await session.refresh(account)

        current_emi = account.emi_amount or simulation["reduce_emi_option"].get("emi_amount")
        emi_change = (current_emi - simulation["reduce_emi_option"]["new_emi_amount"]) if current_emi is not None else None
        tenure_change = None

        # Update account EMI
        account.emi_amount = simulation["reduce_emi_option"]["new_emi_amount"]
    else:  # reduce_tenure
        new_tenure = simulation["reduce_tenure_option"]["new_tenure_months"]
        new_params = {
            "new_principal_balance": new_principal,
            "new_interest_rate": account.interest_rate,
            "new_tenure_months": new_tenure,
            "new_emi_amount": account.emi_amount,
        }
        await regenerate_schedule(session, account_id, from_emi_number, new_params)
        await session.refresh(account)

        tenure_change = simulation["reduce_tenure_option"]["months_saved"]
        emi_change = None

        # Update account tenure
        remaining_before = account.tenure_months - (from_emi_number - 1)
        account.tenure_months = account.tenure_months - (remaining_before - new_tenure)

    # Create prepayment record
    prepayment = LoanPrepayment(
        id=uuid.uuid4(),
        account_id=account_id,
        workspace_id=account.workspace_id,
        transaction_id=transaction_id,
        prepayment_amount=prepayment_amount,
        prepayment_date=prepayment_date,
        recalculation_method=method,
        schedule_version_before=old_version,
        schedule_version_after=account.current_schedule_version,
        tenure_change_months=tenure_change,
        emi_change_amount=emi_change,
    )
    session.add(prepayment)

    # Update account totals
    account.total_prepayments += prepayment_amount
    account.balance = new_principal

    await session.commit()
    await session.refresh(prepayment)
    return prepayment



async def skip_emi(
    session: AsyncSession,
    entry_id: uuid.UUID,
    shift_subsequent: bool = True,
) -> LoanAmortizationSchedule:
    """Mark an EMI as skipped; optionally push later due dates by one month."""
    from dateutil.relativedelta import relativedelta

    result = await session.execute(select(LoanAmortizationSchedule).where(LoanAmortizationSchedule.id == entry_id))
    entry = result.scalar_one()
    entry.payment_status = "skipped"
    entry.notes = ((entry.notes or "") + " | EMI skipped").strip(" |")

    if shift_subsequent:
        later = await session.execute(
            select(LoanAmortizationSchedule).where(
                LoanAmortizationSchedule.account_id == entry.account_id,
                LoanAmortizationSchedule.schedule_version == entry.schedule_version,
                LoanAmortizationSchedule.emi_number > entry.emi_number,
                LoanAmortizationSchedule.payment_status == "scheduled",
            )
        )
        for later_entry in later.scalars().all():
            later_entry.due_date = later_entry.due_date + relativedelta(months=1)

    await session.commit()
    await session.refresh(entry)
    return entry
