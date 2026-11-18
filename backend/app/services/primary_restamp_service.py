"""Re-derive a user's stored primary amounts in their current display currency.

`amount_primary` (and the asset's `purchase_price_primary`) is a cache of a
row's value in its owner's primary currency, written by
`stamp_primary_amount` when the row is created or edited (ADR 0008). When the
owner changes their display currency, every cached value is in the old
currency; income, expense, budget and report totals sum those values and
would print the old numbers under the new symbol (#1381).

`restamp_user_primary_amounts` rewrites them from the native amount and the
rate stored for each row's own date, so it is idempotent: running it twice
gives the same result as running it once. A row whose currency already is the
new primary currency gets its amount at rate 1. A foreign row with no
resolvable rate is set to NULL, the honest "not converted" state that
`restamp_fallback_fx` heals later, rather than keeping a number in the old
currency.

Rows are matched by `user_id`, the same owner `stamp_primary_amount` converts
for, so a member's switch leaves rows other members created alone.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.asset import Asset
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.services.fx_rate_service import _resolve_rate

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")


@dataclass
class RestampStats:
    currency: str
    transactions: int = 0
    recurring: int = 0
    assets: int = 0
    unconverted: int = 0
    _rates: dict[tuple[str, Optional[date]], Optional[Decimal]] = field(
        default_factory=dict, repr=False
    )

    def as_dict(self) -> dict:
        return {
            "currency": self.currency,
            "transactions": self.transactions,
            "recurring": self.recurring,
            "assets": self.assets,
            "unconverted": self.unconverted,
        }


async def _rate(
    session: AsyncSession, stats: RestampStats, currency: str, on: Optional[date]
) -> Optional[Decimal]:
    """The stored rate from `currency` into the target on `on`, cached per run.

    Only stored rates are read (exact date, then the closest one), so a
    restamp of a long history does not call the provider once per day.
    """
    key = (currency, on)
    if key not in stats._rates:
        stats._rates[key] = await _resolve_rate(
            session, currency, stats.currency, on, allow_fetch=False
        )
    return stats._rates[key]


async def _converted(
    session: AsyncSession,
    stats: RestampStats,
    amount: Optional[Decimal],
    currency: str,
    on: Optional[date],
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """(primary amount, rate) for one row; (None, None) when no rate exists."""
    if amount is None:
        return None, None
    amount = Decimal(str(amount))
    if currency == stats.currency:
        return amount.quantize(CENT), Decimal("1")
    rate = await _rate(session, stats, currency, on)
    if rate is None:
        stats.unconverted += 1
        return None, None
    return (amount * rate).quantize(CENT), rate


async def _warm_today_rates(
    session: AsyncSession, stats: RestampStats, currencies: set[str]
) -> None:
    """Let one on-demand fetch bring today's rates when none are stored.

    A currency nobody used before has no rate in `fx_rates` yet; without one
    every foreign row would land as unconverted. This is the same on-demand
    fetch `stamp_primary_amount` does, made once instead of once per row.
    """
    today = app_today()
    for currency in sorted(currencies - {stats.currency}):
        rate = await _resolve_rate(session, currency, stats.currency, today)
        stats._rates[(currency, today)] = rate


async def restamp_user_primary_amounts(
    session: AsyncSession, user_id: uuid.UUID
) -> dict:
    """Rewrite the user's stored primary amounts in their current currency.

    Flushes but does not commit; the caller owns the transaction.
    """
    user = await session.get(User, user_id)
    if user is None:
        return {}
    stats = RestampStats(currency=user.primary_currency)

    transactions = (
        await session.execute(select(Transaction).where(Transaction.user_id == user_id))
    ).scalars().all()
    recurring = (
        await session.execute(
            select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
        )
    ).scalars().all()
    assets = (
        await session.execute(
            select(Asset).where(Asset.user_id == user_id, Asset.purchase_price.is_not(None))
        )
    ).scalars().all()

    await _warm_today_rates(
        session,
        stats,
        {t.currency for t in transactions}
        | {r.currency for r in recurring}
        | {a.currency for a in assets},
    )

    for tx in transactions:
        primary, rate = await _converted(session, stats, tx.amount, tx.currency, tx.date)
        if (tx.amount_primary, tx.fx_rate_used) != (primary, rate):
            tx.amount_primary, tx.fx_rate_used = primary, rate
            stats.transactions += 1

    for rec in recurring:
        # Same date the recurring paths stamp with (`restamp_recurring_fx`).
        primary, rate = await _converted(
            session, stats, rec.amount, rec.currency, rec.start_date
        )
        if (rec.amount_primary, rec.fx_rate_used) != (primary, rate):
            rec.amount_primary, rec.fx_rate_used = primary, rate
            stats.recurring += 1

    for asset in assets:
        primary, _ = await _converted(
            session, stats, asset.purchase_price, asset.currency, asset.purchase_date
        )
        if asset.purchase_price_primary != primary:
            asset.purchase_price_primary = primary
            stats.assets += 1

    await session.flush()
    logger.info("Restamped primary amounts for user %s: %s", user_id, stats.as_dict())
    return stats.as_dict()
