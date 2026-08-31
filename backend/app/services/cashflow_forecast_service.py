import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.forecast import (
    CadenceEnum,
    CashflowForecastResponse,
    DailyForecastPoint,
    FlowDirectionEnum,
    ForecastSummary,
    SubscriptionStatusEnum,
)
from app.services import recurring_detector_service


def _date_matches_cadence(target_date: date, anchor_date: date, cadence: CadenceEnum) -> bool:
    """Determine if a recurring item falls on target_date given anchor_date and cadence."""
    if target_date < anchor_date:
        return False

    days_diff = (target_date - anchor_date).days
    if cadence == CadenceEnum.WEEKLY:
        return days_diff % 7 == 0
    elif cadence == CadenceEnum.BI_WEEKLY:
        return days_diff % 14 == 0
    elif cadence == CadenceEnum.MONTHLY:
        # Match day-of-month (handling shorter months)
        if target_date.day == anchor_date.day:
            return True
        # If anchor is 29, 30, 31 and month ends on 28/30
        next_day = target_date + timedelta(days=1)
        if next_day.day == 1 and anchor_date.day > target_date.day:
            return True
        return False
    elif cadence == CadenceEnum.QUARTERLY:
        return (target_date.month - anchor_date.month) % 3 == 0 and target_date.day == anchor_date.day
    elif cadence == CadenceEnum.SEMI_ANNUAL:
        return (target_date.month - anchor_date.month) % 6 == 0 and target_date.day == anchor_date.day
    elif cadence == CadenceEnum.ANNUAL:
        return target_date.month == anchor_date.month and target_date.day == anchor_date.day
    return False


async def generate_cashflow_forecast(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    horizon_days: int = 90,
    include_discretionary_burn: bool = True,
    reference_date: Optional[date] = None,
) -> CashflowForecastResponse:
    """Generate day-by-day predictive cashflow simulation and liquidity runway metrics."""
    today = reference_date or date.today()
    end_date = today + timedelta(days=horizon_days)

    # 1. Fetch current liquid balance across workspace accounts
    acc_query = (
        select(Account)
        .outerjoin(BankConnection)
        .where(
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
            Account.is_closed.is_(False),
        )
    )
    acc_result = await session.execute(acc_query)
    accounts = list(acc_result.scalars().all())

    # Liquid funds = checking, savings, cash (credit card balances represent debt)
    starting_liquid_balance = Decimal("0.00")
    for acc in accounts:
        balance_val = Decimal(str(acc.balance_primary if acc.balance_primary is not None else acc.balance))
        if acc.type in ("checking", "savings", "cash", "depository"):
            starting_liquid_balance += balance_val
        elif acc.type == "credit_card":
            # Subtract credit card debt from net liquid position
            starting_liquid_balance -= abs(balance_val)

    starting_liquid_balance = starting_liquid_balance.quantize(Decimal("0.01"))

    # 2. Fetch manual recurring transactions in workspace
    rec_query = select(RecurringTransaction).where(
        and_(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active.is_(True),
        )
    )
    rec_result = await session.execute(rec_query)
    manual_recurrings = list(rec_result.scalars().all())

    # 3. Detect historical recurring patterns
    detected_res = await recurring_detector_service.detect_recurring_patterns(
        session, workspace_id, min_occurrences=2, lookback_days=180, reference_date=today
    )

    # Deduplicate: if an auto-detected item shares payee with manual recurring, prefer manual
    manual_payee_set = {
        (r.description or "").lower().strip() for r in manual_recurrings if r.description
    }

    # Combined scheduled items list: [(name, direction, amount, next_date, cadence)]
    schedule_items = []
    for r in manual_recurrings:
        direction = FlowDirectionEnum.INFLOW if r.type == "credit" else FlowDirectionEnum.OUTFLOW
        cadence_map = {
            "weekly": CadenceEnum.WEEKLY,
            "biweekly": CadenceEnum.BI_WEEKLY,
            "monthly": CadenceEnum.MONTHLY,
            "quarterly": CadenceEnum.QUARTERLY,
            "yearly": CadenceEnum.ANNUAL,
            "annual": CadenceEnum.ANNUAL,
        }
        cadence = cadence_map.get(str(r.frequency).lower(), CadenceEnum.MONTHLY)
        schedule_items.append(
            (
                r.description or "Scheduled Recurring",
                direction,
                Decimal(str(r.amount)),
                r.next_occurrence,
                cadence,
            )
        )

    for item in detected_res.items:
        if item.status != SubscriptionStatusEnum.ACTIVE:
            continue
        if item.merchant_name.lower().strip() in manual_payee_set:
            continue
        schedule_items.append(
            (
                item.merchant_name,
                item.direction,
                item.average_amount,
                item.next_expected_date,
                item.cadence,
            )
        )

    # 4. Calculate historical daily baseline discretionary burn (last 90 days)
    daily_discretionary_burn = Decimal("0.00")
    if include_discretionary_burn:
        hist_start = today - timedelta(days=90)
        hist_query = (
            select(Transaction)
            .where(
                and_(
                    Transaction.workspace_id == workspace_id,
                    Transaction.transfer_pair_id.is_(None),
                    Transaction.date >= hist_start,
                    Transaction.date < today,
                    Transaction.type == "debit",
                    Transaction.status == "posted",
                )
            )
        )
        hist_result = await session.execute(hist_query)
        hist_txs = list(hist_result.scalars().all())

        # Filter out recurring items from discretionary calculation
        detected_names_lower = {item.normalized_key.lower() for item in detected_res.items}
        discretionary_txs = [
            t for t in hist_txs
            if recurring_detector_service.normalize_merchant_name(t.payee or t.description).lower() not in detected_names_lower
            and (t.payee or t.description or "").lower().strip() not in manual_payee_set
        ]

        if discretionary_txs:
            total_discretionary = sum(Decimal(str(t.amount_primary or t.amount)) for t in discretionary_txs)
            daily_discretionary_burn = (total_discretionary / Decimal("90")).quantize(Decimal("0.01"))

    # 5. Day-by-day simulation loop
    daily_points: list[DailyForecastPoint] = []
    current_balance = starting_liquid_balance
    lowest_balance = starting_liquid_balance
    lowest_date = today

    total_inflow = Decimal("0.00")
    total_outflow = Decimal("0.00")
    total_discretionary = Decimal("0.00")

    runway_days: Optional[int] = None
    has_shortfall = False
    first_shortfall_date: Optional[date] = None

    for day_idx in range(horizon_days + 1):
        sim_date = today + timedelta(days=day_idx)
        day_inflow = Decimal("0.00")
        day_outflow = Decimal("0.00")
        day_events = []

        # Find recurring items matching today
        for name, direction, amount, anchor, cadence in schedule_items:
            if _date_matches_cadence(sim_date, anchor, cadence):
                if direction == FlowDirectionEnum.INFLOW:
                    day_inflow += amount
                    day_events.append(f"+{amount:.2f} {name}")
                else:
                    day_outflow += amount
                    day_events.append(f"-{amount:.2f} {name}")

        day_burn = daily_discretionary_burn if day_idx > 0 else Decimal("0.00")
        day_net = (day_inflow - day_outflow - day_burn).quantize(Decimal("0.01"))
        start_bal = current_balance
        end_bal = (start_bal + day_net).quantize(Decimal("0.01"))

        if day_idx > 0:
            total_inflow += day_inflow
            total_outflow += day_outflow
            total_discretionary += day_burn

        if end_bal < lowest_balance:
            lowest_balance = end_bal
            lowest_date = sim_date

        if end_bal < Decimal("0.00") and not has_shortfall:
            has_shortfall = True
            first_shortfall_date = sim_date
            if runway_days is None:
                runway_days = day_idx

        daily_points.append(
            DailyForecastPoint(
                date=sim_date,
                starting_balance=start_bal,
                recurring_inflow=day_inflow.quantize(Decimal("0.01")),
                recurring_outflow=day_outflow.quantize(Decimal("0.01")),
                discretionary_burn=day_burn.quantize(Decimal("0.01")),
                net_change=day_net,
                ending_balance=end_bal,
                events=day_events,
            )
        )
        current_balance = end_bal

    net_cashflow = (current_balance - starting_liquid_balance).quantize(Decimal("0.01"))

    summary = ForecastSummary(
        horizon_days=horizon_days,
        start_date=today,
        end_date=end_date,
        starting_liquid_balance=starting_liquid_balance,
        projected_ending_balance=current_balance,
        lowest_projected_balance=lowest_balance,
        lowest_balance_date=lowest_date,
        total_projected_inflow=total_inflow.quantize(Decimal("0.01")),
        total_projected_outflow=total_outflow.quantize(Decimal("0.01")),
        total_projected_discretionary=total_discretionary.quantize(Decimal("0.01")),
        net_cashflow=net_cashflow,
        runway_days=runway_days,
        has_shortfall_risk=has_shortfall,
        first_shortfall_date=first_shortfall_date,
    )

    return CashflowForecastResponse(
        workspace_id=workspace_id,
        summary=summary,
        daily_trajectory=daily_points,
    )
