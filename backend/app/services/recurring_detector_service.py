import math
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.forecast import (
    CadenceEnum,
    DetectedRecurringItem,
    FlowDirectionEnum,
    RecurringDetectionResponse,
    SubscriptionStatusEnum,
)

# Common subscription & utility indicators
SUBSCRIPTION_KEYWORDS = {
    "netflix", "spotify", "apple", "google", "prime", "amazon prime", "hulu",
    "disney", "youtube", "patreon", "github", "openai", "chatgpt", "midjourney",
    "claude", "anthropic", "adobe", "figma", "notion", "slack", "zoom", "dropbox",
    "aws", "digitalocean", "heroku", "vercel", "cloudflare", "hosting", "domain",
    "gym", "fitness", "crossfit", "planet fitness", "equinox",
    "internet", "broadband", "verizon", "att", "tmobile", "comcast", "xfinity",
    "electric", "water", "utility", "insurance", "geico", "state farm", "progressive",
    "rent", "lease", "mortgage", "subscription", "membership", "sub", "recurring"
}

NOISE_PREFIX_REGEX = re.compile(
    r"^(pos\s+debit|pos\s+purchase|ach\s+debit|ach\s+credit|debit\s+card\s+purchase|"
    r"direct\s+debit|recurring\s+payment|pre-authorized\s+payment|card\s+purchase|"
    r"purchase\s+authorized\s+on\s+\d{2}/\d{2}|payment\s+to|transfer\s+to|sq\s*\*|tst\s*\*|"
    r"sp\s*\*|amzn\s+mktp(\s+[a-z]{2})?\s*\*?|paypal\s*\*|intl\s+pos)\s*",
    re.IGNORECASE,
)


DATE_AND_ID_CLEANUP_REGEX = re.compile(r"(\b\d{2}/\d{2}\b|\b\d{4}-\d{2}-\d{2}\b|#\s*\w+|\b\d{4,}\b|\*+)", re.IGNORECASE)


def normalize_merchant_name(raw_name: Optional[str]) -> str:
    """Normalize a raw transaction description or payee into a canonical merchant key."""
    if not raw_name:
        return "Unknown"

    cleaned = NOISE_PREFIX_REGEX.sub("", raw_name.strip())
    cleaned = DATE_AND_ID_CLEANUP_REGEX.sub("", cleaned)
    # Remove trailing city/state/country abbreviations like 'CA', 'NY', 'CA USA'
    cleaned = re.sub(r"\s+[A-Z]{2}(\s+[A-Z]{2,3})?$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else raw_name.strip()




def calculate_cadence(average_interval: float) -> tuple[CadenceEnum, float]:
    """Classify the interval into a standard financial recurrence cadence."""
    if 5.5 <= average_interval <= 8.5:
        return CadenceEnum.WEEKLY, 7.0
    elif 12.0 <= average_interval <= 17.0:
        return CadenceEnum.BI_WEEKLY, 14.0
    elif 25.0 <= average_interval <= 35.0:
        return CadenceEnum.MONTHLY, 30.4375
    elif 80.0 <= average_interval <= 105.0:
        return CadenceEnum.QUARTERLY, 91.25
    elif 170.0 <= average_interval <= 200.0:
        return CadenceEnum.SEMI_ANNUAL, 182.5
    elif 345.0 <= average_interval <= 385.0:
        return CadenceEnum.ANNUAL, 365.25
    return CadenceEnum.IRREGULAR, average_interval


def is_likely_subscription(merchant_name: str, direction: FlowDirectionEnum) -> bool:
    """Detect if a merchant pattern matches recurring consumer/business subscriptions."""
    if direction != FlowDirectionEnum.OUTFLOW:
        return False
    lower_name = merchant_name.lower()
    return any(keyword in lower_name for keyword in SUBSCRIPTION_KEYWORDS)


def compute_next_occurrence(last_date: date, cadence: CadenceEnum, avg_interval: float) -> date:
    """Predict the next expected billing date given historical cadence."""
    if cadence == CadenceEnum.WEEKLY:
        return last_date + timedelta(days=7)
    elif cadence == CadenceEnum.BI_WEEKLY:
        return last_date + timedelta(days=14)
    elif cadence == CadenceEnum.MONTHLY:
        # Approximate monthly calendar jump
        month = last_date.month + 1
        year = last_date.year
        if month > 12:
            month = 1
            year += 1
        # Handle end of month boundary
        day = min(last_date.day, 28)
        try:
            return date(year, month, last_date.day)
        except ValueError:
            return date(year, month, day)
    elif cadence == CadenceEnum.QUARTERLY:
        return last_date + timedelta(days=91)
    elif cadence == CadenceEnum.SEMI_ANNUAL:
        return last_date + timedelta(days=182)
    elif cadence == CadenceEnum.ANNUAL:
        try:
            return date(last_date.year + 1, last_date.month, last_date.day)
        except ValueError:
            return date(last_date.year + 1, last_date.month, 28)
    else:
        return last_date + timedelta(days=max(1, int(round(avg_interval))))


async def detect_recurring_patterns(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    min_occurrences: int = 3,
    lookback_days: int = 365,
    reference_date: Optional[date] = None,
) -> RecurringDetectionResponse:
    """Scan workspace transactions and identify recurring bills, subscriptions, and regular inflows."""
    today = reference_date or date.today()
    start_date = today - timedelta(days=lookback_days)

    query = (
        select(Transaction)
        .where(
            and_(
                Transaction.workspace_id == workspace_id,
                Transaction.transfer_pair_id.is_(None),
                Transaction.date >= start_date,
                Transaction.status == "posted",
            )
        )
        .order_by(Transaction.date.asc())
    )
    result = await session.execute(query)
    transactions = list(result.scalars().all())

    # Cluster transactions by (normalized_name, direction)
    clusters: dict[tuple[str, FlowDirectionEnum], list[Transaction]] = {}
    for tx in transactions:
        norm_name = normalize_merchant_name(tx.payee or tx.description)
        direction = FlowDirectionEnum.INFLOW if tx.type == "credit" else FlowDirectionEnum.OUTFLOW
        key = (norm_name.lower(), direction)
        clusters.setdefault(key, []).append(tx)

    detected_items: list[DetectedRecurringItem] = []
    total_monthly_sub_cost = Decimal("0.00")
    total_annual_sub_cost = Decimal("0.00")
    active_subs_count = 0

    for (norm_key, direction), tx_list in clusters.items():
        if len(tx_list) < min_occurrences:
            continue

        # Sort dates
        sorted_txs = sorted(tx_list, key=lambda t: t.date)
        dates = [t.date for t in sorted_txs]
        amounts = [Decimal(str(t.amount_primary or t.amount)) for t in sorted_txs]
        currencies = [t.currency for t in sorted_txs]
        primary_currency = currencies[-1] if currencies else "USD"

        # Calculate interval deltas
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        # Filter zero-day double charges if any
        positive_intervals = [i for i in intervals if i > 0]
        if not positive_intervals:
            continue

        avg_interval = sum(positive_intervals) / len(positive_intervals)
        cadence, expected_interval = calculate_cadence(avg_interval)

        if cadence == CadenceEnum.IRREGULAR:
            continue

        # Interval variance & standard deviation
        interval_variance = sum((i - expected_interval) ** 2 for i in positive_intervals) / len(positive_intervals)
        interval_std = math.sqrt(interval_variance)

        # Amount metrics
        avg_amount = sum(amounts) / Decimal(str(len(amounts)))
        avg_amount = avg_amount.quantize(Decimal("0.01"))
        amount_variance = sum((float(a) - float(avg_amount)) ** 2 for a in amounts) / len(amounts)
        amount_std = math.sqrt(amount_variance)

        # Confidence Scoring: (0.0 to 1.0)
        # Interval consistency component
        interval_consistency = max(0.1, 1.0 - min(1.0, interval_std / max(1.0, expected_interval)))
        # Amount consistency component
        amount_consistency = max(0.1, 1.0 - min(1.0, amount_std / max(1.0, float(avg_amount))))

        confidence = round(0.65 * interval_consistency + 0.35 * amount_consistency, 2)
        if confidence < 0.40:
            continue

        first_date = dates[0]
        last_date = dates[-1]
        next_date = compute_next_occurrence(last_date, cadence, avg_interval)

        # Advance next_date if it has already passed
        while next_date < today:
            next_date = compute_next_occurrence(next_date, cadence, avg_interval)

        # Status evaluation
        days_since_last = (today - last_date).days
        if days_since_last > (expected_interval * 2.2):
            status = SubscriptionStatusEnum.LAPSED
        elif days_since_last > (expected_interval * 1.5):
            status = SubscriptionStatusEnum.PENDING
        else:
            status = SubscriptionStatusEnum.ACTIVE

        # Annual cost projection
        if cadence == CadenceEnum.WEEKLY:
            annual_impact = avg_amount * Decimal("52")
        elif cadence == CadenceEnum.BI_WEEKLY:
            annual_impact = avg_amount * Decimal("26")
        elif cadence == CadenceEnum.MONTHLY:
            annual_impact = avg_amount * Decimal("12")
        elif cadence == CadenceEnum.QUARTERLY:
            annual_impact = avg_amount * Decimal("4")
        elif cadence == CadenceEnum.SEMI_ANNUAL:
            annual_impact = avg_amount * Decimal("2")
        else:
            annual_impact = avg_amount

        annual_impact = annual_impact.quantize(Decimal("0.01"))
        is_sub = is_likely_subscription(norm_key, direction) or (
            direction == FlowDirectionEnum.OUTFLOW and cadence in (CadenceEnum.MONTHLY, CadenceEnum.ANNUAL)
        )

        if is_sub and status == SubscriptionStatusEnum.ACTIVE:
            active_subs_count += 1
            monthly_equiv = (annual_impact / Decimal("12")).quantize(Decimal("0.01"))
            total_monthly_sub_cost += monthly_equiv
            total_annual_sub_cost += annual_impact

        item = DetectedRecurringItem(
            merchant_name=sorted_txs[-1].payee or sorted_txs[-1].description,
            normalized_key=norm_key,
            direction=direction,
            cadence=cadence,
            average_interval_days=round(avg_interval, 1),
            occurrence_count=len(sorted_txs),
            first_seen_date=first_date,
            last_seen_date=last_date,
            next_expected_date=next_date,
            average_amount=avg_amount,
            currency=primary_currency,
            confidence_score=confidence,
            is_subscription=is_sub,
            status=status,
            estimated_annual_impact=annual_impact,
            category_id=sorted_txs[-1].category_id,
            account_id=sorted_txs[-1].account_id,
            sample_transaction_ids=[t.id for t in sorted_txs[-5:]],
        )
        detected_items.append(item)

    # Sort items: active subscriptions first, then by next expected date
    detected_items.sort(key=lambda x: (0 if x.status == SubscriptionStatusEnum.ACTIVE else 1, x.next_expected_date))

    return RecurringDetectionResponse(
        workspace_id=workspace_id,
        total_detected=len(detected_items),
        active_subscriptions_count=active_subs_count,
        total_monthly_subscription_cost=total_monthly_sub_cost,
        total_annual_subscription_cost=total_annual_sub_cost,
        items=detected_items,
    )
