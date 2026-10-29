import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.schemas.forecast import (
    CadenceEnum,
    CashflowForecastResponse,
    DailyForecastPoint,
    FlowDirectionEnum,
    ForecastSummary,
    SubscriptionStatusEnum,
)
from app.core.config import get_settings
from app.services import account_service, recurring_detector_service
from app.services._query_filters import counts_as_pnl
from app.services.fx_rate_service import convert_or_none
from app.services.recurring_transaction_service import get_occurrences_in_range


def _date_matches_cadence(target_date: date, anchor_date: date, cadence: CadenceEnum) -> bool:
    """Determine if a recurring item falls on target_date given anchor_date and cadence."""
    if target_date < anchor_date:
        return False

    days_diff = (target_date - anchor_date).days
    if cadence == CadenceEnum.WEEKLY:
        return days_diff % 7 == 0
    elif cadence == CadenceEnum.BI_WEEKLY:
        return days_diff % 14 == 0

    month_difference = (
        (target_date.year - anchor_date.year) * 12
        + target_date.month
        - anchor_date.month
    )
    cadence_months = {
        CadenceEnum.MONTHLY: 1,
        CadenceEnum.QUARTERLY: 3,
        CadenceEnum.SEMI_ANNUAL: 6,
        CadenceEnum.ANNUAL: 12,
    }
    if cadence in cadence_months:
        expected_day = min(
            anchor_date.day,
            calendar.monthrange(target_date.year, target_date.month)[1],
        )
        return (
            month_difference % cadence_months[cadence] == 0
            and target_date.day == expected_day
        )
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

    # 1. Start from the same current balance the account screen shows: the
    # transaction-based balance for manual accounts, the provider balance for
    # connected ones. The stored balance column is only the opening balance
    # for manual accounts.
    workspace = await session.get(Workspace, workspace_id)
    base_currency = workspace.default_currency if workspace else get_settings().default_currency
    accounts = await account_service.get_accounts(session, workspace_id)

    # Liquid funds = checking, savings, cash. Credit card balances are already
    # signed (debt is negative), so adding them nets the debt off. Amounts with
    # no FX rate are left out and reported instead of counted 1:1 (#574).
    starting_liquid_balance = Decimal("0.00")
    unconverted_currencies: set[str] = set()
    seen_shared_lines: set[str] = set()
    for acc in accounts:
        if acc["type"] not in ("checking", "savings", "cash", "depository", "credit_card"):
            continue
        # Cards on one consolidated credit line each carry the whole line's
        # debt, so the line is counted once, as on the dashboard.
        shared_line = acc.get("shared_balance_group") if acc["type"] == "credit_card" else None
        if shared_line:
            if shared_line in seen_shared_lines:
                continue
            seen_shared_lines.add(shared_line)
        balance_val, _ = await convert_or_none(
            session,
            Decimal(str(acc["current_balance"])),
            acc["currency"],
            base_currency,
            today,
        )
        if balance_val is None:
            unconverted_currencies.add(acc["currency"])
            continue
        starting_liquid_balance += balance_val

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

    # 3. Detect historical recurring patterns. Transactions already linked to
    # an active manual recurring record are left out: the manual rule schedules
    # them, and their payee may not match the manual description.
    manual_recurring_ids = {r.id for r in manual_recurrings}
    detected_res = await recurring_detector_service.detect_recurring_patterns(
        session,
        workspace_id,
        min_occurrences=2,
        lookback_days=180,
        reference_date=today,
        exclude_recurring_ids=manual_recurring_ids,
    )

    # Deduplicate: if an auto-detected item shares payee with manual recurring, prefer manual
    manual_payee_set = {
        recurring_detector_service.normalize_merchant_name(r.description).lower()
        for r in manual_recurrings
        if r.description
    }

    # Manual recurrings follow the same schedule the dashboard projects:
    # frequency, end date, intended day and weekend adjustment all apply.
    # {date: [(name, direction, amount)]}
    manual_events: dict[date, list[tuple[str, FlowDirectionEnum, Decimal]]] = {}
    for r in manual_recurrings:
        direction = FlowDirectionEnum.INFLOW if r.type == "credit" else FlowDirectionEnum.OUTFLOW
        amount, _ = await convert_or_none(
            session, Decimal(str(r.amount)), r.currency, base_currency, today
        )
        if amount is None:
            unconverted_currencies.add(r.currency)
            continue
        occurrences = get_occurrences_in_range(
            start=r.next_occurrence,
            frequency=r.frequency,
            end_date=r.end_date,
            range_start=today,
            range_end=end_date + timedelta(days=1),
            intended_day=r.day_of_month or r.start_date.day,
            weekend_adjustment=r.weekend_adjustment,
        )
        for occ_date in occurrences:
            manual_events.setdefault(occ_date, []).append(
                (r.description or "Scheduled Recurring", direction, amount)
            )

    # Detected items: [(name, direction, amount, next_date, cadence)]
    schedule_items = []
    for item in detected_res.items:
        if item.status != SubscriptionStatusEnum.ACTIVE:
            continue
        if item.normalized_key.lower() in manual_payee_set:
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
                    counts_as_pnl(),
                    Transaction.source != "opening_balance",
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
            if t.recurring_transaction_id not in manual_recurring_ids
            and recurring_detector_service.normalize_merchant_name(t.payee or t.description).lower() not in detected_names_lower
            and recurring_detector_service.normalize_merchant_name(t.payee or t.description).lower() not in manual_payee_set
        ]

        if discretionary_txs:
            total_discretionary = sum(Decimal(str(t.amount_primary or t.amount)) for t in discretionary_txs)
            history_days = max(1, (today - min(t.date for t in discretionary_txs)).days)
            daily_discretionary_burn = (total_discretionary / Decimal(str(history_days))).quantize(Decimal("0.01"))

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
        day_items = list(manual_events.get(sim_date, []))
        for name, direction, amount, anchor, cadence in schedule_items:
            if _date_matches_cadence(sim_date, anchor, cadence):
                day_items.append((name, direction, amount))
        for name, direction, amount in day_items:
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

        total_inflow += day_inflow
        total_outflow += day_outflow
        if day_idx > 0:
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
        unconverted_currencies=sorted(unconverted_currencies),
    )

    return CashflowForecastResponse(
        workspace_id=workspace_id,
        summary=summary,
        daily_trajectory=daily_points,
    )
