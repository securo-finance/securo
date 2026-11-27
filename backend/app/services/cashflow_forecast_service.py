import calendar
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
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


# Manual recurring frequencies expressed as detector cadences.
_FREQUENCY_TO_CADENCE = {
    "weekly": CadenceEnum.WEEKLY,
    "biweekly": CadenceEnum.BI_WEEKLY,
    "monthly": CadenceEnum.MONTHLY,
    "quarterly": CadenceEnum.QUARTERLY,
    "semiannual": CadenceEnum.SEMI_ANNUAL,
    "yearly": CadenceEnum.ANNUAL,
}

# How far a detected average may sit from a manual rule's amount and still be
# taken for the same bill. Utility and card bills drift a little month to
# month; a different bill on the same cadence rarely lands this close.
_SAME_BILL_AMOUNT_TOLERANCE = Decimal("0.10")

# Accounts whose balance can pay a bill today. Investments and loans are not
# spendable, and card debt is reported apart from the runway.
_LIQUID_ACCOUNT_TYPES = ("checking", "savings", "wallet")

# The discretionary burn is always the spending of this window, however little
# of it there is: one recent purchase is not a daily habit.
_BURN_WINDOW_DAYS = 90


def _names_match(detected_key: str, manual_key: str) -> bool:
    """Same merchant when one normalized name is the other or holds it as whole words."""
    if not detected_key or not manual_key:
        return False
    if detected_key == manual_key:
        return True
    shorter, longer = sorted((detected_key, manual_key), key=len)
    return len(shorter) >= 3 and re.search(rf"\b{re.escape(shorter)}\b", longer) is not None


def _amount_near_rule(amount: Decimal, rule: dict) -> bool:
    """Whether an amount is close enough to a manual rule's to be that bill.

    A rule with no FX rate is not scheduled at all, so nothing is its duplicate.
    """
    expected = rule["amount"]
    if not expected:
        return False
    return abs(abs(amount) - abs(expected)) <= abs(expected) * _SAME_BILL_AMOUNT_TOLERANCE


def _matches_manual_rule(item, rule: dict) -> bool:
    """Whether a detected pattern is a bill a manual rule already schedules.

    Direction, cadence and amount come first: a rule's name inside another
    bill's name proves nothing when those differ. Past them, a matching name is
    enough. Transactions not linked to the rule (imported from the bank, entered
    by hand) often carry an unrelated payee, so without a name match the same
    account and the same category must both agree.
    """
    if item.direction != rule["direction"] or item.cadence != rule["cadence"]:
        return False
    if not _amount_near_rule(item.average_amount, rule):
        return False
    if _names_match(item.normalized_key.lower(), rule["name_key"]):
        return True
    return (
        rule["account_id"] is not None
        and item.account_id == rule["account_id"]
        and rule["category_id"] is not None
        and item.category_id == rule["category_id"]
    )


def _is_manual_bill_payment(name_key: str, amount: Decimal, rule: dict) -> bool:
    """Whether a single unlinked debit is a payment of a manual outflow rule."""
    return (
        rule["direction"] == FlowDirectionEnum.OUTFLOW
        and _amount_near_rule(amount, rule)
        and _names_match(name_key, rule["name_key"])
    )


async def generate_cashflow_forecast(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    horizon_days: int = 90,
    include_discretionary_burn: bool = True,
    reference_date: Optional[date] = None,
) -> CashflowForecastResponse:
    """Generate day-by-day predictive cashflow simulation and liquidity runway metrics."""
    # The workspace's own calendar, like the account balances and dashboard,
    # so the starting balance and the simulation agree on which day is today.
    today = reference_date or app_today()
    end_date = today + timedelta(days=horizon_days)

    # 1. Start from the same current balance the account screen shows: the
    # transaction-based balance for manual accounts, the provider balance for
    # connected ones. The stored balance column is only the opening balance
    # for manual accounts.
    workspace = await session.get(Workspace, workspace_id)
    base_currency = workspace.default_currency if workspace else get_settings().default_currency
    accounts = await account_service.get_accounts(session, workspace_id)

    # The runway measures when the money that pays bills runs out, so it starts
    # from liquid funds alone. Card debt is not due today and netting it off
    # can put a solvent user at zero; new card purchases still reach the burn.
    # Amounts with no FX rate are left out and reported instead of counted 1:1
    # (#574).
    starting_liquid_balance = Decimal("0.00")
    credit_card_balance = Decimal("0.00")
    unconverted_currencies: set[str] = set()
    seen_shared_lines: set[str] = set()
    for acc in accounts:
        is_card = acc["type"] == "credit_card"
        if not is_card and acc["type"] not in _LIQUID_ACCOUNT_TYPES:
            continue
        # Cards on one consolidated credit line each carry the whole line's
        # debt, so the line is counted once, as on the dashboard.
        shared_line = acc.get("shared_balance_group") if is_card else None
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
        if is_card:
            credit_card_balance += balance_val
        else:
            starting_liquid_balance += balance_val

    starting_liquid_balance = starting_liquid_balance.quantize(Decimal("0.01"))
    credit_card_balance = credit_card_balance.quantize(Decimal("0.01"))

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
    unconverted_currencies.update(detected_res.unconverted_currencies)

    # Manual recurrings follow the same schedule the dashboard projects:
    # frequency, end date, intended day and weekend adjustment all apply.
    # {date: [(name, direction, amount)]}
    manual_events: dict[date, list[tuple[str, FlowDirectionEnum, Decimal]]] = {}
    # What each manual rule looks like to the detector, to drop detected
    # patterns that are the same bill under another payee name.
    manual_rules: list[dict] = []
    for r in manual_recurrings:
        direction = FlowDirectionEnum.INFLOW if r.type == "credit" else FlowDirectionEnum.OUTFLOW
        amount, _ = await convert_or_none(
            session, Decimal(str(r.amount)), r.currency, base_currency, today
        )
        manual_rules.append(
            {
                "name_key": recurring_detector_service.normalize_merchant_name(r.description).lower()
                if r.description
                else "",
                "direction": direction,
                "amount": amount,
                "cadence": _FREQUENCY_TO_CADENCE.get(r.frequency),
                "account_id": r.account_id,
                "category_id": r.category_id,
            }
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
    # A pending item is a little late, not gone: its history already leaves
    # the burn below, so dropping it from the schedule would make the expense
    # vanish. Pending outflows stay scheduled at their next expected date,
    # labelled pending. Pending inflows are left out, so a late paycheck
    # cannot hide a shortfall. Lapsed items have stopped and stay out.
    schedule_items = []
    for item in detected_res.items:
        if any(_matches_manual_rule(item, rule) for rule in manual_rules):
            continue
        if item.status == SubscriptionStatusEnum.PENDING:
            if item.direction != FlowDirectionEnum.OUTFLOW:
                continue
            name = f"{item.merchant_name} (pending)"
        elif item.status == SubscriptionStatusEnum.ACTIVE:
            name = item.merchant_name
        else:
            continue
        schedule_items.append(
            (
                name,
                item.direction,
                item.average_amount,
                item.next_expected_date,
                item.cadence,
            )
        )

    # 4. Calculate historical daily baseline discretionary burn
    daily_discretionary_burn = Decimal("0.00")
    if include_discretionary_burn:
        hist_start = today - timedelta(days=_BURN_WINDOW_DAYS)
        reportable = recurring_detector_service.pnl_transaction_amounts(workspace_id)
        hist_query = (
            select(Transaction, reportable.c.amount)
            .join(reportable, reportable.c.transaction_id == Transaction.id)
            .where(
                and_(
                    Transaction.workspace_id == workspace_id,
                    Transaction.source != "opening_balance",
                    Transaction.date >= hist_start,
                    Transaction.date < today,
                    Transaction.type == "debit",
                    Transaction.status == "posted",
                )
            )
        )
        hist_result = await session.execute(hist_query)
        hist_txs = hist_result.all()

        # Filter out recurring items from discretionary calculation
        detected_outflow_keys = {
            item.normalized_key.lower()
            for item in detected_res.items
            if item.direction == FlowDirectionEnum.OUTFLOW
        }
        burn_total = Decimal("0.00")
        for t, pnl_amount in hist_txs:
            if t.recurring_transaction_id in manual_recurring_ids:
                continue
            name_key = recurring_detector_service.normalize_merchant_name(t.payee or t.description).lower()
            if name_key in detected_outflow_keys:
                continue
            amount = await recurring_detector_service.amount_in_currency(
                session, t, base_currency, Decimal(str(pnl_amount))
            )
            if amount is None:
                unconverted_currencies.add(t.currency)
                continue
            if any(_is_manual_bill_payment(name_key, amount, rule) for rule in manual_rules):
                continue
            burn_total += amount

        daily_discretionary_burn = (burn_total / Decimal(_BURN_WINDOW_DAYS)).quantize(Decimal("0.01"))

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
        credit_card_balance=credit_card_balance,
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
