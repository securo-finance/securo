import uuid
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule


def calculate_emi(principal: Decimal, annual_rate: Decimal, tenure_months: int) -> Decimal:
    """Calculate EMI using reducing balance method.

    Formula: EMI = P × r × (1 + r)^n / ((1 + r)^n - 1)
    Where:
        P = Principal amount
        r = Monthly interest rate (annual_rate / 12 / 100)
        n = Tenure in months
    """
    if annual_rate == Decimal("0.00"):
        # Zero interest: EMI = principal / tenure
        return (principal / Decimal(tenure_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    monthly_rate = annual_rate / Decimal("1200")  # annual_rate / 12 / 100
    numerator = principal * monthly_rate * ((Decimal("1") + monthly_rate) ** tenure_months)
    denominator = ((Decimal("1") + monthly_rate) ** tenure_months) - Decimal("1")
    emi = (numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return emi


async def generate_amortization_schedule(
    session: AsyncSession, account_id: uuid.UUID, version: int = 1
) -> list[LoanAmortizationSchedule]:
    """Generate full amortization schedule for a loan account.

    Uses reducing balance method to calculate principal and interest
    components for each EMI.
    """
    # Fetch loan account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    if account.type != "loan":
        raise ValueError(f"Account {account_id} is not a loan account")

    if not all([account.original_principal, account.interest_rate, account.tenure_months, account.disbursed_on]):
        raise ValueError(f"Account {account_id} missing required loan fields")

    principal = account.original_principal
    annual_rate = account.interest_rate
    tenure_months = account.tenure_months
    disbursed_on = account.disbursed_on
    emi_day = account.emi_day or disbursed_on.day

    # Calculate EMI if not provided
    emi_amount = account.emi_amount
    if not emi_amount:
        emi_amount = calculate_emi(principal, annual_rate, tenure_months)

    monthly_rate = annual_rate / Decimal("1200") if annual_rate > 0 else Decimal("0")
    remaining_principal = principal
    entries = []

    for i in range(1, tenure_months + 1):
        # Calculate due date: disbursed_on + i months, adjusted to emi_day
        due_date = disbursed_on + relativedelta(months=i)
        # Adjust to emi_day, handling month-end edge cases
        try:
            due_date = due_date.replace(day=emi_day)
        except ValueError:
            # emi_day exceeds days in month (e.g., 31 in Feb), use last day
            due_date = due_date + relativedelta(day=31)

        opening_balance = remaining_principal
        interest_component = (opening_balance * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal_component = emi_amount - interest_component

        # Last EMI: principal_component = remaining balance (avoid rounding residual)
        if i == tenure_months:
            principal_component = opening_balance
            interest_component = emi_amount - principal_component

        closing_balance = opening_balance - principal_component
        remaining_principal = closing_balance

        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            account_id=account_id,
            workspace_id=account.workspace_id,
            schedule_version=version,
            emi_number=i,
            due_date=due_date,
            principal_component=principal_component,
            interest_component=interest_component,
            emi_amount=emi_amount,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            payment_status="scheduled",
        )
        entries.append(entry)
        session.add(entry)

    # Persist computed EMI on the account when the caller left it blank.
    if account.emi_amount is None and entries:
        account.emi_amount = entries[0].emi_amount
    account.current_schedule_version = version

    await session.commit()
    return entries


async def get_schedule(
    session: AsyncSession,
    account_id: uuid.UUID,
    version: Optional[int] = None,
    filters: Optional[dict] = None,
) -> list[LoanAmortizationSchedule]:
    """Retrieve amortization schedule with optional filters."""
    # Fetch current version if not specified
    if version is None:
        result = await session.execute(select(Account.current_schedule_version).where(Account.id == account_id))
        version = result.scalar_one()

    query = select(LoanAmortizationSchedule).where(
        LoanAmortizationSchedule.account_id == account_id, LoanAmortizationSchedule.schedule_version == version
    )

    if filters:
        if "payment_status" in filters:
            query = query.where(LoanAmortizationSchedule.payment_status == filters["payment_status"])
        if "from_date" in filters:
            query = query.where(LoanAmortizationSchedule.due_date >= filters["from_date"])
        if "to_date" in filters:
            query = query.where(LoanAmortizationSchedule.due_date <= filters["to_date"])

    query = query.order_by(LoanAmortizationSchedule.emi_number)

    result = await session.execute(query)
    return list(result.scalars().all())


async def update_schedule_entry(
    session: AsyncSession, entry_id: uuid.UUID, updates: dict
) -> tuple[LoanAmortizationSchedule, int]:
    """Update a single schedule entry.

    Returns:
        tuple: (updated_entry, affected_entries_count)
        affected_entries_count > 0 if recalculation needed for subsequent entries
    """
    result = await session.execute(select(LoanAmortizationSchedule).where(LoanAmortizationSchedule.id == entry_id))
    entry = result.scalar_one()

    # Track if recalculation needed
    needs_recalc = False
    if "principal_component" in updates or "interest_component" in updates:
        needs_recalc = True

    # Apply updates
    for key, value in updates.items():
        if hasattr(entry, key):
            setattr(entry, key, value)

    await session.commit()
    await session.refresh(entry)

    affected_count = 0
    if needs_recalc:
        # Recalculate subsequent entries (simplified: just mark as affected)
        # Full recalculation logic would be in regenerate_schedule
        result = await session.execute(
            select(LoanAmortizationSchedule).where(
                LoanAmortizationSchedule.account_id == entry.account_id,
                LoanAmortizationSchedule.schedule_version == entry.schedule_version,
                LoanAmortizationSchedule.emi_number > entry.emi_number,
            )
        )
        affected_count = len(list(result.scalars().all()))

    return entry, affected_count


async def bulk_update_dates(
    session: AsyncSession,
    account_id: uuid.UUID,
    shift_days: Optional[int] = None,
    new_emi_day: Optional[int] = None,
    from_emi_number: Optional[int] = None,
) -> int:
    """Bulk update schedule dates.

    Args:
        shift_days: Shift all dates by N days
        new_emi_day: Change day-of-month for all dates
        from_emi_number: Apply changes from this EMI onwards

    Returns:
        Number of entries updated
    """
    # Fetch current version
    result = await session.execute(select(Account.current_schedule_version).where(Account.id == account_id))
    version = result.scalar_one()

    query = select(LoanAmortizationSchedule).where(
        LoanAmortizationSchedule.account_id == account_id, LoanAmortizationSchedule.schedule_version == version
    )

    if from_emi_number:
        query = query.where(LoanAmortizationSchedule.emi_number >= from_emi_number)

    result = await session.execute(query)
    entries = list(result.scalars().all())

    for entry in entries:
        if shift_days:
            entry.due_date = entry.due_date + timedelta(days=shift_days)
        elif new_emi_day:
            try:
                entry.due_date = entry.due_date.replace(day=new_emi_day)
            except ValueError:
                # Day exceeds month length, use last day
                entry.due_date = entry.due_date + relativedelta(day=31)

    await session.commit()
    return len(entries)


async def regenerate_schedule(
    session: AsyncSession, account_id: uuid.UUID, from_emi_number: int, new_params: dict
) -> list[LoanAmortizationSchedule]:
    """Regenerate schedule from a specific EMI number with new parameters.

    Creates a new schedule version, preserving old entries.

    Args:
        from_emi_number: Starting EMI number (1-indexed)
        new_params: dict with keys:
            - new_principal_balance: Decimal
            - new_interest_rate: Optional[Decimal]
            - new_tenure_months: Optional[int]
            - new_emi_amount: Optional[Decimal]
    """
    # Fetch account and increment version
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    old_version = account.current_schedule_version
    new_version = old_version + 1
    account.current_schedule_version = new_version

    # Carry forward historical EMIs (paid/partial/etc.) into the new version so
    # get_schedule(current) still shows progress after prepay/rate-change.
    # Return value still lists only newly generated future EMIs.
    if from_emi_number > 1:
        hist_result = await session.execute(
            select(LoanAmortizationSchedule)
            .where(
                LoanAmortizationSchedule.account_id == account_id,
                LoanAmortizationSchedule.schedule_version == old_version,
                LoanAmortizationSchedule.emi_number < from_emi_number,
            )
            .order_by(LoanAmortizationSchedule.emi_number)
        )
        for old in hist_result.scalars().all():
            copied = LoanAmortizationSchedule(
                id=uuid.uuid4(),
                account_id=account_id,
                workspace_id=account.workspace_id,
                schedule_version=new_version,
                emi_number=old.emi_number,
                due_date=old.due_date,
                principal_component=old.principal_component,
                interest_component=old.interest_component,
                emi_amount=old.emi_amount,
                opening_balance=old.opening_balance,
                closing_balance=old.closing_balance,
                payment_status=old.payment_status,
                actual_payment_date=old.actual_payment_date,
                actual_amount_paid=old.actual_amount_paid,
                linked_transaction_id=old.linked_transaction_id,
                notes=old.notes,
            )
            session.add(copied)

    # Extract new parameters
    new_principal = new_params["new_principal_balance"]
    new_rate = new_params.get("new_interest_rate", account.interest_rate)
    new_tenure = new_params.get("new_tenure_months")
    new_emi = new_params.get("new_emi_amount")

    # If tenure not specified, calculate remaining tenure
    if not new_tenure:
        new_tenure = account.tenure_months - (from_emi_number - 1)

    # Calculate new EMI if not provided
    if not new_emi:
        new_emi = calculate_emi(new_principal, new_rate, new_tenure)

    monthly_rate = new_rate / Decimal("1200") if new_rate > 0 else Decimal("0")
    remaining_principal = new_principal
    entries: list[LoanAmortizationSchedule] = []

    # Get the due date of the previous EMI to calculate subsequent dates
    if from_emi_number > 1:
        prev_result = await session.execute(
            select(LoanAmortizationSchedule.due_date)
            .where(
                LoanAmortizationSchedule.account_id == account_id,
                LoanAmortizationSchedule.emi_number == from_emi_number - 1,
            )
            .order_by(LoanAmortizationSchedule.schedule_version.desc())
            .limit(1)
        )
        base_date = prev_result.scalar_one()
    else:
        base_date = account.disbursed_on

    emi_day = account.emi_day or base_date.day

    for i in range(new_tenure):
        emi_num = from_emi_number + i
        due_date = base_date + relativedelta(months=i + 1)
        try:
            due_date = due_date.replace(day=emi_day)
        except ValueError:
            due_date = due_date + relativedelta(day=31)

        opening_balance = remaining_principal
        interest_component = (opening_balance * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal_component = new_emi - interest_component

        if i == new_tenure - 1:
            principal_component = opening_balance
            interest_component = new_emi - principal_component

        closing_balance = opening_balance - principal_component
        remaining_principal = closing_balance

        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            account_id=account_id,
            workspace_id=account.workspace_id,
            schedule_version=new_version,
            emi_number=emi_num,
            due_date=due_date,
            principal_component=principal_component,
            interest_component=interest_component,
            emi_amount=new_emi,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            payment_status="scheduled",
        )
        entries.append(entry)
        session.add(entry)

    await session.commit()
    return entries
