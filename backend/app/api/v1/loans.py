"""Loan schedule API routes — wraps loan_* services for the frontend."""
from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import date
from decimal import Decimal, ROUND_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_workspace_access
from app.core.workspace_context import WorkspaceContext, current_writable_workspace, current_workspace
from app.models.account import Account
from app.models.loan_prepayment import LoanPrepayment
from app.models.loan_schedule import LoanAmortizationSchedule
from app.schemas.loan_schedule import (
    AutoLinkRequest,
    LoanScheduleEntryRead,
    LoanScheduleEntryUpdate,
    MarkPaymentStatusRequest,
    PrepaymentCreate,
    PrepaymentRead,
)
from app.schemas.loan_commitment import (
    CombinedSimulationRequest,
    CommitmentCreate,
    CommitmentRead,
)
from app.services import (
    loan_analytics_service,
    loan_payment_service,
    loan_schedule_service,
    loan_simulation_service,
)
from app.services import loan_combined_simulation_service, loan_commitment_service


router = APIRouter()


# --- Request bodies used by routes that take account_id in JSON ---


class BulkDateUpdate(BaseModel):
    account_id: uuid.UUID
    shift_days: Optional[int] = None
    new_day_of_month: Optional[int] = Field(None, ge=1, le=31)
    from_emi_number: Optional[int] = Field(None, ge=1)


class PrepaymentSimulateBody(BaseModel):
    account_id: uuid.UUID
    prepayment_amount: Decimal = Field(..., gt=0)
    prepayment_date: date


class PrepaymentRecordBody(PrepaymentCreate):
    account_id: uuid.UUID


class AutoLinkBody(AutoLinkRequest):
    account_id: uuid.UUID


class GenerateScheduleBody(BaseModel):
    version: int = 1


# ---------------------------------------------------------------------------
# Static / collection routes FIRST (before /{account_id}/...)
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    """Dashboard summary shaped for LoanDashboardWidget."""
    summary = await loan_analytics_service.get_dashboard_summary(db, workspace_id)
    next_due = [
        {
            "account_id": p["loan_id"],
            "account_name": p["loan_name"],
            "due_date": p["due_date"].isoformat() if hasattr(p["due_date"], "isoformat") else p["due_date"],
            "emi_amount": str(p["emi_amount"]),
            "days_until_due": p["days_until_due"],
        }
        for p in summary.get("next_due_payments", [])
    ]
    recent = [
        {
            "account_name": p["loan_name"],
            "payment_date": (
                p["payment_date"].isoformat()
                if p.get("payment_date") and hasattr(p["payment_date"], "isoformat")
                else p.get("payment_date")
            ),
            "amount_paid": str(p["amount"]),
        }
        for p in summary.get("recent_payments", [])
    ]
    alerts = [
        {
            "type": a.get("type", "info"),
            "message": a.get("message", ""),
            "account_id": a.get("loan_id", ""),
        }
        for a in summary.get("alerts", [])
    ]
    return {
        "next_due_payments": next_due,
        "recent_payments": recent,
        "alerts": alerts,
        "total_monthly_emi": str(summary.get("total_monthly_emi", 0)),
        "total_outstanding": str(summary.get("total_outstanding", 0)),
        "active_loans_count": summary.get("active_loans_count", 0),
        "ytd_principal_paid": summary.get("ytd_principal_paid"),
        "ytd_interest_paid": summary.get("ytd_interest_paid"),
    }


@router.get("/debt-ratios")
async def calculate_debt_ratios(
    monthly_income: Optional[Decimal] = None,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    return await loan_analytics_service.calculate_debt_ratios(
        db, workspace_id, monthly_income=monthly_income
    )


@router.get("/summary")
async def get_loan_summary(
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    result = await db.execute(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.type == "loan",
            Account.is_closed == False,  # noqa: E712
        )
    )
    accounts = list(result.scalars().all())
    total_outstanding = sum(abs(acc.balance or Decimal("0")) for acc in accounts)
    total_emi = sum(acc.emi_amount or Decimal("0") for acc in accounts)
    return {
        "total_loans": len(accounts),
        "total_outstanding": str(total_outstanding),
        "total_monthly_emi": str(total_emi),
        "accounts": [
            {"id": str(acc.id), "name": acc.display_name or acc.name, "balance": str(acc.balance)}
            for acc in accounts
        ],
    }


@router.post("/calculate-emi")
async def calculate_emi(calc_data: dict):
    principal = Decimal(str(calc_data["principal"]))
    annual_rate = Decimal(str(calc_data["annual_rate"]))
    tenure_months = int(calc_data["tenure_months"])
    if principal <= 0 or tenure_months <= 0:
        raise HTTPException(status_code=422, detail="Principal and tenure must be positive")
    emi = loan_schedule_service.calculate_emi(principal, annual_rate, tenure_months)
    total_payment = emi * tenure_months
    total_interest = total_payment - principal
    return {
        "emi_amount": str(emi),
        "total_interest": str(total_interest),
        "total_payment": str(total_payment),
    }


@router.post("/calculate-savings")
async def calculate_prepayment_savings(savings_data: dict):
    remaining_principal = Decimal(str(savings_data["remaining_principal"]))
    annual_rate = Decimal(str(savings_data["annual_rate"]))
    remaining_months = int(savings_data["remaining_months"])
    prepayment_amount = Decimal(str(savings_data["prepayment_amount"]))

    original_emi = loan_schedule_service.calculate_emi(
        remaining_principal, annual_rate, remaining_months
    )
    original_total = original_emi * remaining_months
    original_interest = original_total - remaining_principal
    new_principal = remaining_principal - prepayment_amount

    new_emi = loan_schedule_service.calculate_emi(new_principal, annual_rate, remaining_months)
    new_total_reduce_emi = new_emi * remaining_months
    new_interest_reduce_emi = new_total_reduce_emi - new_principal
    savings_reduce_emi = original_interest - new_interest_reduce_emi

    if annual_rate == Decimal("0.00"):
        new_months = int((new_principal / original_emi).quantize(Decimal("1"), rounding=ROUND_UP))
    else:
        monthly_rate = annual_rate / Decimal("1200")
        new_months = math.ceil(
            math.log(float(original_emi / (original_emi - new_principal * monthly_rate)))
            / math.log(1 + float(monthly_rate))
        )
    new_total_reduce_tenure = original_emi * new_months
    new_interest_reduce_tenure = new_total_reduce_tenure - new_principal
    savings_reduce_tenure = original_interest - new_interest_reduce_tenure
    months_saved = remaining_months - new_months

    return {
        "interest_saved_reduce_emi": str(savings_reduce_emi),
        "interest_saved_reduce_tenure": str(savings_reduce_tenure),
        "months_saved_reduce_tenure": months_saved,
        "new_emi_reduce_emi": str(new_emi),
        "new_tenure_reduce_tenure": new_months,
    }


@router.post("/validate-schedule")
async def validate_schedule(
    validate_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account_id = uuid.UUID(validate_data["account_id"])
    result = await db.execute(
        select(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.account_id == account_id,
            LoanAmortizationSchedule.workspace_id == workspace_id,
        )
        .order_by(LoanAmortizationSchedule.schedule_version, LoanAmortizationSchedule.emi_number)
    )
    entries = list(result.scalars().all())
    issues: list[str] = []
    is_valid = True
    for i, entry in enumerate(entries):
        expected_closing = entry.opening_balance - entry.principal_component
        if abs(entry.closing_balance - expected_closing) > Decimal("0.01"):
            issues.append(f"EMI {entry.emi_number}: Balance mismatch")
            is_valid = False
        expected_emi = entry.principal_component + entry.interest_component
        if abs(entry.emi_amount - expected_emi) > Decimal("0.01"):
            issues.append(f"EMI {entry.emi_number}: EMI component mismatch")
            is_valid = False
        if i > 0 and entries[i - 1].schedule_version == entry.schedule_version:
            if abs(entry.opening_balance - entries[i - 1].closing_balance) > Decimal("0.01"):
                issues.append(f"EMI {entry.emi_number}: Opening balance doesn't match previous closing")
                is_valid = False
    return {"is_valid": is_valid, "issues": issues}


@router.post("/simulations/early-payment")
async def simulate_early_payment(
    simulation_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account_id = uuid.UUID(str(simulation_data["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    prepayment_amount = Decimal(str(simulation_data["prepayment_amount"]))
    prepayment_date = date.fromisoformat(
        simulation_data.get("prepayment_date", date.today().isoformat())
    )
    try:
        return await loan_simulation_service.simulate_early_payment(
            session=db,
            account_id=account_id,
            prepayment_amount=prepayment_amount,
            prepayment_date=prepayment_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/simulations/preclosure")
async def simulate_preclosure(
    simulation_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account_id = uuid.UUID(str(simulation_data["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    closure_date = date.fromisoformat(
        simulation_data.get("closure_date", date.today().isoformat())
    )
    try:
        return await loan_simulation_service.simulate_preclosure(
            session=db, account_id=account_id, closure_date=closure_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/simulations/interest-rate-change")
async def simulate_interest_rate_change(
    simulation_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account_id = uuid.UUID(str(simulation_data["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    new_interest_rate = Decimal(str(simulation_data["new_interest_rate"]))
    effective_from_date = date.fromisoformat(
        simulation_data.get("effective_from_date", date.today().isoformat())
    )
    try:
        return await loan_simulation_service.simulate_interest_rate_change(
            session=db,
            account_id=account_id,
            new_interest_rate=new_interest_rate,
            effective_from_date=effective_from_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@router.post("/apply/preclosure")
async def apply_preclosure(
    body: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    """Apply a preclosure: skip remaining EMIs, zero balance, mark account closed."""
    account_id = uuid.UUID(str(body["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    closure_date = date.fromisoformat(body["closure_date"]) if body.get("closure_date") else date.today()
    try:
        return await loan_simulation_service.apply_preclosure(
            session=db, account_id=account_id, closure_date=closure_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/apply/interest-rate-change")
async def apply_interest_rate_change(
    body: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    """Apply a new interest rate and regenerate remaining schedule."""
    account_id = uuid.UUID(str(body["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    new_interest_rate = Decimal(str(body["new_interest_rate"]))
    effective_from_date = (
        date.fromisoformat(body["effective_from_date"])
        if body.get("effective_from_date")
        else date.today()
    )
    strategy = body.get("strategy", "keep_tenure")
    try:
        return await loan_simulation_service.apply_interest_rate_change(
            session=db,
            account_id=account_id,
            new_interest_rate=new_interest_rate,
            effective_from_date=effective_from_date,
            strategy=strategy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc




@router.post("/simulations/combined")
async def simulate_combined_scenarios(
    body: CombinedSimulationRequest,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    """Combined scenario ground: prepay + recurring + rate changes (+ EMI holiday) interplay."""
    await _require_loan_account(db, body.account_id, workspace_id)
    try:
        return await loan_combined_simulation_service.simulate_combined(
            session=db,
            account_id=body.account_id,
            events=[e.model_dump(mode="json") for e in body.events],
            strategy=body.strategy,
            as_of_date=body.as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/commitments", response_model=list[CommitmentRead])
async def list_loan_commitments(
    account_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    return await loan_commitment_service.list_commitments(
        db, workspace_id, account_id=account_id
    )


@router.post("/commitments", response_model=CommitmentRead)
async def create_loan_commitment(
    body: CommitmentCreate,
    db: AsyncSession = Depends(get_db),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    await _require_loan_account(db, body.account_id, ctx.workspace.id)
    try:
        return await loan_commitment_service.commit_plan_action(
            db,
            workspace_id=ctx.workspace.id,
            user_id=ctx.user.id,
            account_id=body.account_id,
            kind=body.kind,
            amount=body.amount,
            start_date=body.start_date,
            end_date=body.end_date,
            day_of_month=body.day_of_month,
            funding_account_id=body.funding_account_id,
            category_id=body.category_id,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/commitments/{commitment_id}/cancel", response_model=CommitmentRead)
async def cancel_loan_commitment(
    commitment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    try:
        return await loan_commitment_service.cancel_commitment(
            db, commitment_id, ctx.workspace.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/prepayments/simulate")
async def simulate_prepayment(
    body: PrepaymentSimulateBody,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, body.account_id, workspace_id)
    return await loan_payment_service.simulate_prepayment(
        session=db,
        account_id=body.account_id,
        prepayment_amount=body.prepayment_amount,
        prepayment_date=body.prepayment_date,
    )


@router.post("/prepayments", response_model=PrepaymentRead)
async def record_prepayment(
    body: PrepaymentRecordBody,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, body.account_id, workspace_id)
    prepayment = await loan_payment_service.record_prepayment(
        session=db,
        account_id=body.account_id,
        prepayment_amount=body.prepayment_amount,
        prepayment_date=body.prepayment_date,
        method=body.recalculation_method,
        transaction_id=body.transaction_id,
    )
    return prepayment


@router.post("/schedule/bulk-update-dates")
async def bulk_update_dates(
    update_data: BulkDateUpdate,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, update_data.account_id, workspace_id)
    updated_count = await loan_schedule_service.bulk_update_dates(
        session=db,
        account_id=update_data.account_id,
        shift_days=update_data.shift_days,
        new_emi_day=update_data.new_day_of_month,
        from_emi_number=update_data.from_emi_number,
    )
    return {"updated_count": updated_count}


@router.post("/schedule/regenerate")
async def regenerate_schedule(
    regenerate_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account_id = uuid.UUID(str(regenerate_data["account_id"]))
    await _require_loan_account(db, account_id, workspace_id)
    from_emi_number = int(regenerate_data["from_emi_number"])
    if from_emi_number < 1:
        raise HTTPException(status_code=422, detail="from_emi_number must be >= 1")
    new_params = {
        "new_principal_balance": Decimal(str(regenerate_data["new_principal"])),
        "new_interest_rate": Decimal(str(regenerate_data["new_annual_rate"])),
        "new_tenure_months": regenerate_data.get("new_tenure_months"),
        "new_emi_amount": (
            Decimal(str(regenerate_data["new_emi_amount"]))
            if regenerate_data.get("new_emi_amount") is not None
            else None
        ),
    }
    entries = await loan_schedule_service.regenerate_schedule(
        session=db,
        account_id=account_id,
        from_emi_number=from_emi_number,
        new_params=new_params,
    )
    new_version = entries[0].schedule_version if entries else None
    return {"new_schedule_version": new_version, "entries_created": len(entries)}


@router.post("/schedule/auto-link")
async def auto_link_transactions(
    link_data: AutoLinkBody,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, link_data.account_id, workspace_id)
    matches, auto_linked, requires_review = await loan_payment_service.auto_link_transactions(
        session=db,
        account_id=link_data.account_id,
        date_tolerance_days=link_data.date_tolerance_days,
        amount_tolerance_pct=link_data.amount_tolerance_pct,
    )
    return {
        "linked_count": auto_linked,
        "requires_review_count": requires_review,
        "matches": matches,
    }


@router.post("/schedule/bulk-mark-status")
async def bulk_mark_status(
    bulk_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    entry_ids = [uuid.UUID(id_str) for id_str in bulk_data["entry_ids"]]
    payment_status = bulk_data["payment_status"]
    if payment_status not in ["scheduled", "paid", "partial", "missed", "skipped"]:
        raise HTTPException(status_code=422, detail="Invalid payment_status")
    from sqlalchemy import update

    stmt = (
        update(LoanAmortizationSchedule)
        .where(
            LoanAmortizationSchedule.id.in_(entry_ids),
            LoanAmortizationSchedule.workspace_id == workspace_id,
        )
        .values(payment_status=payment_status)
    )
    result = await db.execute(stmt)
    await db.commit()
    return {"updated_count": result.rowcount}


@router.patch("/schedule/{entry_id}", response_model=LoanScheduleEntryRead)
async def update_schedule_entry(
    entry_id: uuid.UUID,
    update_data: LoanScheduleEntryUpdate,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    entry = await _get_schedule_entry(db, entry_id, workspace_id)
    updated, _ = await loan_schedule_service.update_schedule_entry(
        session=db,
        entry_id=entry_id,
        updates=update_data.model_dump(exclude_unset=True),
    )
    return updated


@router.put("/schedule/{entry_id}/status", response_model=LoanScheduleEntryRead)
async def mark_entry_status(
    entry_id: uuid.UUID,
    status_data: MarkPaymentStatusRequest,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _get_schedule_entry(db, entry_id, workspace_id)
    return await loan_payment_service.mark_payment_status(
        session=db,
        entry_id=entry_id,
        status=status_data.payment_status,
        actual_date=status_data.actual_payment_date,
        actual_amount=status_data.actual_amount_paid,
    )


@router.post("/schedule/{entry_id}/link", response_model=LoanScheduleEntryRead)
async def manual_link_transaction(
    entry_id: uuid.UUID,
    link_data: dict,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _get_schedule_entry(db, entry_id, workspace_id)
    transaction_id = uuid.UUID(str(link_data["transaction_id"]))
    return await loan_payment_service.link_transaction_to_entry(
        session=db, entry_id=entry_id, transaction_id=transaction_id
    )


# ---------------------------------------------------------------------------
# Per-account routes
# ---------------------------------------------------------------------------


@router.get("/{account_id}/schedule", response_model=list[LoanScheduleEntryRead])
async def get_loan_schedule(
    account_id: uuid.UUID,
    status: Optional[str] = Query(None, pattern="^(scheduled|paid|partial|missed|skipped)$"),
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, account_id, workspace_id)
    filters: dict = {}
    if status:
        filters["payment_status"] = status
    if from_date:
        filters["from_date"] = from_date
    if to_date:
        filters["to_date"] = to_date
    return await loan_schedule_service.get_schedule(
        session=db,
        account_id=account_id,
        filters=filters or None,
    )


@router.get("/{account_id}/schedule/export")
async def export_schedule_csv(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, account_id, workspace_id)
    entries = await loan_schedule_service.get_schedule(session=db, account_id=account_id)
    if not entries:
        raise HTTPException(status_code=404, detail="No schedule found")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "EMI Number",
            "Due Date",
            "Principal",
            "Interest",
            "EMI Amount",
            "Opening Balance",
            "Closing Balance",
            "Status",
        ]
    )
    for entry in entries:
        writer.writerow(
            [
                entry.emi_number,
                entry.due_date.isoformat(),
                str(entry.principal_component),
                str(entry.interest_component),
                str(entry.emi_amount),
                str(entry.opening_balance),
                str(entry.closing_balance),
                entry.payment_status,
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=loan_{account_id}_schedule.csv"},
    )


@router.post("/{account_id}/schedule/generate", response_model=list[LoanScheduleEntryRead])
async def generate_schedule(
    account_id: uuid.UUID,
    body: GenerateScheduleBody | None = None,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    """Generate (or regenerate v1) amortization schedule from account loan fields."""
    await _require_loan_account(db, account_id, workspace_id)
    version = body.version if body else 1
    # Clear existing entries for this version so re-seed is idempotent
    existing = await loan_schedule_service.get_schedule(
        session=db, account_id=account_id, version=version
    )
    if existing:
        for e in existing:
            await db.delete(e)
        await db.commit()
    try:
        entries = await loan_schedule_service.generate_amortization_schedule(
            session=db, account_id=account_id, version=version
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return entries


@router.get("/{account_id}/prepayments", response_model=list[PrepaymentRead])
async def list_prepayments(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, account_id, workspace_id)
    result = await db.execute(
        select(LoanPrepayment)
        .where(
            LoanPrepayment.account_id == account_id,
            LoanPrepayment.workspace_id == workspace_id,
        )
        .order_by(LoanPrepayment.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{account_id}/overview")
async def get_loan_overview(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    account = await _require_loan_account(db, account_id, workspace_id)
    overview = await loan_analytics_service.get_loan_overview(db, account_id)
    remaining_entries_interest = Decimal("0")
    remaining_principal = Decimal(str(overview.get("current_outstanding", 0)))
    schedule = await loan_schedule_service.get_schedule(session=db, account_id=account_id)
    remaining = [e for e in schedule if e.payment_status == "scheduled"]
    if remaining:
        remaining_principal = sum((e.principal_component for e in remaining), Decimal("0"))
        remaining_entries_interest = sum((e.interest_component for e in remaining), Decimal("0"))
    return {
        "progress_percent": overview.get("progress_pct", 0),
        "progress_pct": overview.get("progress_pct", 0),
        "emis_paid": overview.get("emis_paid", 0),
        "emis_remaining": overview.get("emis_remaining", 0),
        "principal_paid": str(overview.get("principal_paid", 0)),
        "principal_remaining": str(remaining_principal),
        "interest_paid": str(overview.get("interest_paid", 0)),
        "interest_remaining": str(remaining_entries_interest),
        "total_prepayments": str(account.total_prepayments or 0),
        "original_principal": str(overview.get("original_principal", 0)),
        "current_outstanding": str(overview.get("current_outstanding", 0)),
        "total_paid": str(overview.get("total_paid", 0)),
    }


@router.get("/{account_id}/breakdown")
async def get_yearly_breakdown(
    account_id: uuid.UUID,
    group_by: str = Query("year", pattern="^(year|quarter|month)$"),
    db: AsyncSession = Depends(get_db),
    workspace_id: uuid.UUID = Depends(require_workspace_access),
):
    await _require_loan_account(db, account_id, workspace_id)
    return await loan_analytics_service.get_yearly_breakdown(db, account_id, group_by=group_by)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_loan_account(
    db: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID
) -> Account:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


async def _get_schedule_entry(
    db: AsyncSession, entry_id: uuid.UUID, workspace_id: uuid.UUID
) -> LoanAmortizationSchedule:
    result = await db.execute(
        select(LoanAmortizationSchedule).where(
            LoanAmortizationSchedule.id == entry_id,
            LoanAmortizationSchedule.workspace_id == workspace_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Schedule entry not found")
    return entry
