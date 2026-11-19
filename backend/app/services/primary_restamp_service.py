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
new primary currency gets its amount at rate 1. A row in another currency than
its account, when the new primary is the account's currency, takes what the
account was charged (`amount_account`, ADR 0027), not a market conversion.
Balances read `amount_account`, which this never writes, so no balance moves
when the display currency does. A foreign row with no
resolvable rate is set to NULL, the honest "not converted" state, rather than
keeping a number in the old currency. The scheduled `restamp_fallback_fx`
heals such transactions once a rate arrives; an asset's
`purchase_price_primary` is healed by `backfill_primary_amounts`, by editing
the asset, or by running `restamp-primary-amounts` again.

Rows are matched by `user_id`, the same owner `stamp_primary_amount` converts
for, so a member's switch leaves rows other members created alone.

The restamp only reads stored rates and never commits. Fetching today's quotes
from the provider commits the session, so callers do it first, with
`fetch_missing_today_rates`, before any write they still have to commit.
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
from app.core.currency import same_currency
from app.models.account import Account
from app.models.asset import Asset
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.services.fx_rate_service import _get_exact_date_rate, _resolve_rate, sync_rates

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
# `fx_rate_used` is Numeric(20, 10): compare and store at that scale, or a
# rerun would see 1/6 and the stored 0.1666666667 as a change.
RATE_SCALE = Decimal("1E-10")


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
    return (amount * rate).quantize(CENT), rate.quantize(RATE_SCALE)


def _charged_in_primary(
    stats: RestampStats, tx: Transaction, account_currency: str
) -> Optional[tuple[Decimal, Decimal]]:
    """What the account was charged, when the new primary is its currency.

    A foreign row keeps its value in its account's currency (`amount_account`,
    ADR 0027): the bank's charge or the amount the user typed. When the
    display currency becomes that account's, it is the row's primary amount,
    so a USD purchase the card billed at R$ 545.10 reads 545.10 again after a
    round trip through USD, not a market conversion.
    """
    if (
        tx.amount_account is None
        or not tx.amount
        or same_currency(tx.currency, account_currency)
        or not same_currency(account_currency, stats.currency)
    ):
        return None
    primary = Decimal(str(tx.amount_account)).quantize(CENT)
    return primary, (primary / abs(Decimal(str(tx.amount)))).quantize(RATE_SCALE)


async def _row_currencies(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    currencies: set[str] = set()
    for column, owner in (
        (Transaction.currency, Transaction.user_id),
        (RecurringTransaction.currency, RecurringTransaction.user_id),
        (Asset.currency, Asset.user_id),
    ):
        rows = await session.execute(select(column).where(owner == user_id).distinct())
        currencies.update(c for c in rows.scalars() if c)
    return currencies


async def fetch_missing_today_rates(
    session: AsyncSession, user_id: uuid.UUID, currency: str
) -> None:
    """Fetch today's quotes once when one the restamp needs is not stored.

    A currency nobody used before has no rate in `fx_rates` yet; without one
    every foreign row would land as unconverted. This is the same on-demand
    fetch `stamp_primary_amount` does, made once for the whole restamp, and a
    provider failure only leaves the stored history to fall back on.

    `sync_rates` commits the session, so call this before making any change
    the caller still has to commit.
    """
    today = app_today()
    for code in sorted(await _row_currencies(session, user_id) | {currency}):
        if await _get_exact_date_rate(session, code, today) is None:
            break
    else:
        return
    try:
        await sync_rates(session, today)
    except Exception:
        logger.warning("FX rate fetch before a restamp failed for %s", today, exc_info=True)


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
        await session.execute(
            select(Transaction, Account.currency)
            .join(Account, Account.id == Transaction.account_id)
            .where(Transaction.user_id == user_id)
        )
    ).all()
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

    for tx, account_currency in transactions:
        charged = _charged_in_primary(stats, tx, account_currency)
        if charged is not None:
            primary, rate = charged
        else:
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
