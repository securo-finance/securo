import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule
from app.models.loan_prepayment import LoanPrepayment


async def get_loan_overview(session: AsyncSession, account_id: uuid.UUID) -> dict:
    """Get comprehensive loan overview metrics."""
    # Fetch account
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Get schedule entries for current version
    schedule_result = await session.execute(
        select(LoanAmortizationSchedule).where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.schedule_version == account.current_schedule_version,
        )
    )
    entries = list(schedule_result.scalars().all())

    # Calculate aggregates
    paid_entries = [e for e in entries if e.payment_status == "paid"]
    remaining_entries = [e for e in entries if e.payment_status == "scheduled"]

    principal_paid = sum(e.principal_component for e in paid_entries)
    interest_paid = sum(e.interest_component for e in paid_entries)
    total_paid = principal_paid + interest_paid

    total_remaining_principal = sum(e.principal_component for e in remaining_entries)
    total_remaining_interest = sum(e.interest_component for e in remaining_entries)

    progress_pct = (float(principal_paid) / float(account.original_principal) * 100) if account.original_principal else 0

    return {
        "original_principal": float(account.original_principal or 0),
        "current_outstanding": float(account.balance),
        "principal_paid": float(principal_paid),
        "interest_paid": float(interest_paid),
        "total_paid": float(total_paid),
        "progress_pct": progress_pct,
        "emis_paid": len(paid_entries),
        "emis_remaining": len(remaining_entries),
    }


async def get_yearly_breakdown(session: AsyncSession, account_id: uuid.UUID, group_by: str = "year") -> list[dict]:
    """Get principal vs interest breakdown by period."""
    # Fetch schedule entries
    result = await session.execute(
        select(LoanAmortizationSchedule)
        .where(LoanAmortizationSchedule.account_id == account_id)
        .order_by(LoanAmortizationSchedule.schedule_version.desc(), LoanAmortizationSchedule.emi_number)
    )
    entries = list(result.scalars().all())

    # Group by period
    breakdown = {}
    for entry in entries:
        if group_by == "year":
            period = str(entry.due_date.year)
        elif group_by == "quarter":
            quarter = (entry.due_date.month - 1) // 3 + 1
            period = f"{entry.due_date.year}-Q{quarter}"
        elif group_by == "month":
            period = entry.due_date.strftime("%Y-%m")
        else:
            period = str(entry.due_date.year)

        if period not in breakdown:
            breakdown[period] = {
                "period": period,
                "principal_component": Decimal("0"),
                "interest_component": Decimal("0"),
                "total_paid": Decimal("0"),
                "prepayments": Decimal("0"),
                "closing_balance": Decimal("0"),
                "emis_scheduled": 0,
                "emis_paid": 0,
            }

        breakdown[period]["principal_component"] += entry.principal_component
        breakdown[period]["interest_component"] += entry.interest_component
        breakdown[period]["total_paid"] += entry.emi_amount
        breakdown[period]["closing_balance"] = entry.closing_balance
        breakdown[period]["emis_scheduled"] += 1
        if entry.payment_status == "paid":
            breakdown[period]["emis_paid"] += 1

    # Fetch prepayments and add to breakdown
    prepay_result = await session.execute(
        select(LoanPrepayment).where(LoanPrepayment.account_id == account_id)
    )
    prepayments = list(prepay_result.scalars().all())

    for prepay in prepayments:
        if group_by == "year":
            period = str(prepay.prepayment_date.year)
        elif group_by == "quarter":
            quarter = (prepay.prepayment_date.month - 1) // 3 + 1
            period = f"{prepay.prepayment_date.year}-Q{quarter}"
        elif group_by == "month":
            period = prepay.prepayment_date.strftime("%Y-%m")
        else:
            period = str(prepay.prepayment_date.year)

        if period in breakdown:
            breakdown[period]["prepayments"] += prepay.prepayment_amount

    # Convert to list and format
    result = []
    for period_data in sorted(breakdown.values(), key=lambda x: x["period"]):
        result.append({
            "period": period_data["period"],
            "principal_component": float(period_data["principal_component"]),
            "interest_component": float(period_data["interest_component"]),
            "total_paid": float(period_data["total_paid"]),
            "prepayments": float(period_data["prepayments"]),
            "closing_balance": float(period_data["closing_balance"]),
            "emis_scheduled": period_data["emis_scheduled"],
            "emis_paid": period_data["emis_paid"],
        })

    return result


async def calculate_debt_ratios(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    loan_ids: Optional[list[uuid.UUID]] = None,
    monthly_income: Optional[Decimal] = None,
) -> dict:
    """Calculate debt-to-income and other ratios."""
    # Fetch loan accounts
    query = select(Account).where(Account.workspace_id == workspace_id, Account.type == "loan", Account.is_closed == False)
    if loan_ids:
        query = query.where(Account.id.in_(loan_ids))

    result = await session.execute(query)
    loans = list(result.scalars().all())

    # Calculate aggregates
    total_outstanding = sum(loan.balance for loan in loans)
    total_monthly_emi = sum(loan.emi_amount or Decimal("0") for loan in loans)
    total_original_principal = sum(loan.original_principal or Decimal("0") for loan in loans)

    # Weighted average interest rate
    if total_original_principal > 0:
        weighted_avg_rate = sum(
            (loan.interest_rate or Decimal("0")) * (loan.original_principal or Decimal("0")) for loan in loans
        ) / total_original_principal
    else:
        weighted_avg_rate = Decimal("0")

    # Calculate ratios
    debt_ratios = {}
    if monthly_income:
        debt_ratios["debt_to_income_ratio"] = float(total_monthly_emi / monthly_income)
        debt_ratios["emi_to_income_ratio"] = float(total_monthly_emi / monthly_income)

    debt_ratios["loan_to_value_ratios"] = []  # Would need asset linkage

    # Status assessment
    dti = debt_ratios.get("debt_to_income_ratio", 0)
    if dti < 0.36:
        status = "healthy"
    elif dti < 0.50:
        status = "caution"
    else:
        status = "high_risk"

    return {
        "aggregate_metrics": {
            "total_outstanding": float(total_outstanding),
            "total_monthly_emi": float(total_monthly_emi),
            "total_original_principal": float(total_original_principal),
            "weighted_avg_interest_rate": float(weighted_avg_rate),
        },
        "debt_ratios": debt_ratios,
        "comparison": {
            "recommended_dti": 0.36,
            "recommended_emi_to_income": 0.40,
            "status": status,
        },
    }


async def get_dashboard_summary(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    """Get aggregate loan metrics for dashboard widget."""
    # Fetch active loans
    result = await session.execute(
        select(Account).where(Account.workspace_id == workspace_id, Account.type == "loan", Account.is_closed == False)
    )
    loans = list(result.scalars().all())

    active_loans_count = len(loans)
    total_outstanding = sum(loan.balance for loan in loans)
    total_monthly_emi = sum(loan.emi_amount or Decimal("0") for loan in loans)

    # YTD principal and interest
    current_year = date.today().year
    ytd_query = select(
        func.sum(LoanAmortizationSchedule.principal_component).label("principal"),
        func.sum(LoanAmortizationSchedule.interest_component).label("interest"),
    ).where(
        LoanAmortizationSchedule.workspace_id == workspace_id,
        LoanAmortizationSchedule.payment_status == "paid",
        func.extract("year", LoanAmortizationSchedule.actual_payment_date) == current_year,
    )
    ytd_result = await session.execute(ytd_query)
    ytd_row = ytd_result.one()

    # Next due payments (current schedule version only; skip closed loans)
    next_due_query = (
        select(LoanAmortizationSchedule)
        .join(Account, LoanAmortizationSchedule.account_id == Account.id)
        .where(
            LoanAmortizationSchedule.workspace_id == workspace_id,
            LoanAmortizationSchedule.payment_status == "scheduled",
            LoanAmortizationSchedule.due_date >= date.today(),
            LoanAmortizationSchedule.schedule_version == Account.current_schedule_version,
            Account.is_closed == False,  # noqa: E712
        )
        .order_by(LoanAmortizationSchedule.due_date)
        .limit(5)
    )
    next_due_result = await session.execute(next_due_query)
    next_due_entries = list(next_due_result.scalars().all())

    # Recent payments
    recent_query = (
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.workspace_id == workspace_id,
            LoanAmortizationSchedule.payment_status == "paid",
        )
        .order_by(LoanAmortizationSchedule.actual_payment_date.desc())
        .limit(5)
    )
    recent_result = await session.execute(recent_query)
    recent_entries = list(recent_result.scalars().all())

    # Build response
    next_due_payments = []
    for entry in next_due_entries:
        loan_result = await session.execute(select(Account).where(Account.id == entry.account_id))
        loan = loan_result.scalar_one()
        days_until = (entry.due_date - date.today()).days
        next_due_payments.append({
            "loan_id": str(loan.id),
            "loan_name": loan.display_name or loan.name,
            "emi_amount": float(entry.emi_amount),
            "due_date": entry.due_date,
            "days_until_due": days_until,
        })

    recent_payments = []
    for entry in recent_entries:
        loan_result = await session.execute(select(Account).where(Account.id == entry.account_id))
        loan = loan_result.scalar_one()
        recent_payments.append({
            "loan_id": str(loan.id),
            "loan_name": loan.display_name or loan.name,
            "amount": float(entry.actual_amount_paid or entry.emi_amount),
            "payment_date": entry.actual_payment_date,
            "principal_component": float(entry.principal_component),
            "interest_component": float(entry.interest_component),
        })

    # Alerts
    alerts = []
    for entry in next_due_entries[:3]:
        days_until = (entry.due_date - date.today()).days
        loan_result = await session.execute(select(Account).where(Account.id == entry.account_id))
        loan = loan_result.scalar_one()

        if days_until <= 3:
            alerts.append({
                "type": "payment_due",
                "loan_id": str(loan.id),
                "loan_name": loan.display_name or loan.name,
                "message": f"EMI due in {days_until} days",
                "severity": "warning" if days_until > 0 else "error",
            })

    return {
        "active_loans_count": active_loans_count,
        "total_outstanding": float(total_outstanding),
        "total_monthly_emi": float(total_monthly_emi),
        "total_principal_paid_ytd": float(ytd_row.principal or 0),
        "total_interest_paid_ytd": float(ytd_row.interest or 0),
        "next_due_payments": next_due_payments,
        "recent_payments": recent_payments,
        "alerts": alerts,
    }
