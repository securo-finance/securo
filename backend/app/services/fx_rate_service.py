import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.fx_rate import FxRate
from app.models.user import User
from app.providers.openexchangerates import OpenExchangeRatesProvider

logger = logging.getLogger(__name__)

_provider = OpenExchangeRatesProvider()

# Owned by one bank import, never by the process or a pooled session.
FetchedRateCache = dict[date, dict[str, Decimal]]


async def _fetch_supported_rates(target: date) -> dict[str, Decimal]:
    supported = set(get_settings().supported_currencies.split(","))
    if target == app_today():
        rates = await _provider.fetch_latest()
    else:
        rates = await _provider.fetch_historical(target)
    return {currency: rate for currency, rate in rates.items() if currency in supported}


async def sync_rates(
    session: AsyncSession, target_date: Optional[date] = None,
) -> int:
    """Fetch rates from the provider for the given date and upsert into fx_rates.

    Only saves rates for currencies in `supported_currencies`.
    Idempotent — existing rates for the same date are updated.
    Returns the number of rates synced. This standalone cache refresh commits;
    atomic bank imports use a non-persisting, import-local rate cache instead.
    """
    requested_target = target_date or app_today()
    # Providers cannot return a historical rate for a date that has not
    # happened yet. Treat a future request as a request for today's latest
    # published rate and store it under today's date.
    target = min(requested_target, app_today())
    rates = await _fetch_supported_rates(target)

    count = 0
    # Standalone refreshes write one date per transaction. Keep currency locks
    # ordered too, regardless of the provider's response ordering.
    for currency_code, rate in sorted(rates.items()):
        stmt = pg_insert(FxRate).values(
            base_currency="USD",
            quote_currency=currency_code,
            date=target,
            rate=rate,
            source=_provider.name,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fx_rate_base_quote_date",
            set_={"rate": rate, "source": _provider.name},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d FX rates for %s", count, target)
    return count


async def _resolve_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
    persist_fetched_rates: bool = True,
    fetched_rates: Optional[FetchedRateCache] = None,
) -> Optional[Decimal]:
    """Resolve the true FX rate, or None when no rate can be found.

    Uses cross-rate through USD: rate = usd_to_target / usd_to_source.
    Priority: exact date → on-demand fetch → closest available.
    Returns None (not a fake 1:1) when no rate is available, so callers that
    persist a conversion can honestly leave it NULL instead of storing a wrong
    amount (issue #353). Pass ``allow_fetch=False`` to skip the on-demand
    provider call and rely only on already-stored rates. Bank imports pass
    ``persist_fetched_rates=False`` and an import-local ``fetched_rates`` cache:
    their conversions stay accurate without acquiring shared FX write locks.
    Missing historical rates fetched by an import are not persisted to fx_rates.
    """
    if from_currency == to_currency:
        return Decimal("1")

    requested_target = target_date or app_today()
    # A future transaction must use the latest real rate, never query a
    # not-yet-existing historical snapshot. Looking up against today also
    # lets the closest-rate fallback select the last cached business day.
    target = min(requested_target, app_today())

    if not persist_fetched_rates and fetched_rates is None:
        fetched_rates = {}

    # Step 1: Try exact date, including this import's earlier fetched snapshot.
    usd_to_source = await _get_exact_date_rate(session, from_currency, target, fetched_rates)
    usd_to_target = await _get_exact_date_rate(session, to_currency, target, fetched_rates)

    # Step 2: If missing, fetch from provider for exact date
    if allow_fetch and (usd_to_source is None or usd_to_target is None):
        try:
            if persist_fetched_rates:
                synced = await sync_rates(session, target)
            else:
                assert fetched_rates is not None
                if target not in fetched_rates:
                    fetched_rates[target] = await _fetch_supported_rates(target)
                synced = len(fetched_rates[target])
            if synced > 0:
                logger.info("On-demand sync fetched %d rates for %s", synced, target)
                if usd_to_source is None:
                    usd_to_source = await _get_exact_date_rate(
                        session, from_currency, target, fetched_rates,
                    )
                if usd_to_target is None:
                    usd_to_target = await _get_exact_date_rate(
                        session, to_currency, target, fetched_rates,
                    )
        except Exception:
            if not persist_fetched_rates and fetched_rates is not None:
                # Do not hammer a failing provider once per transaction row.
                # A new import gets a fresh cache and can retry this date.
                fetched_rates.setdefault(target, {})
            logger.warning("On-demand FX rate sync failed for %s", target, exc_info=True)

    # Step 3: Fall back to closest available date
    if usd_to_source is None:
        usd_to_source = await _get_closest_rate(session, from_currency, target, fetched_rates)
    if usd_to_target is None:
        usd_to_target = await _get_closest_rate(session, to_currency, target, fetched_rates)

    if from_currency == "USD":
        usd_to_source = Decimal("1")
    if to_currency == "USD":
        usd_to_target = Decimal("1")

    if usd_to_source is None or usd_to_target is None or usd_to_source == 0:
        return None

    return usd_to_target / usd_to_source


async def get_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> Decimal:
    """Get FX rate from from_currency to to_currency.

    Uses cross-rate through USD. When no rate can be resolved, returns a 1:1
    fallback so live reads (balances, dashboards) still render a number. This
    fallback is NOT meant to be persisted — persisting paths use
    :func:`_resolve_rate` directly and leave the value NULL instead (issue #353).
    """
    rate = await _resolve_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    if rate is None:
        logger.warning(
            "No FX rate found for %s -> %s on %s, returning 1",
            from_currency, to_currency, target_date or app_today(),
        )
        return Decimal("1")
    return rate


async def _get_exact_date_rate(
    session: AsyncSession, currency: str, target: date,
    fetched_rates: Optional[FetchedRateCache] = None,
) -> Optional[Decimal]:
    """Get the rate for an exact date."""
    if currency == "USD":
        return Decimal("1")
    if fetched_rates is not None and currency in fetched_rates.get(target, {}):
        return fetched_rates[target][currency]
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date == target,
        )
    )
    return result


async def _get_closest_rate(
    session: AsyncSession, currency: str, target: date,
    fetched_rates: Optional[FetchedRateCache] = None,
) -> Optional[Decimal]:
    """Closest stored/import-local snapshot, preferring before, then after."""
    if currency == "USD":
        return Decimal("1")
    local = [
        (day, rates[currency]) for day, rates in (fetched_rates or {}).items()
        if currency in rates
    ]
    before = [entry for entry in local if entry[0] <= target]
    stored = (await session.execute(
        select(FxRate.date, FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date <= target,
        )
        .order_by(desc(FxRate.date))
        .limit(1)
    )).first()
    if stored is not None:
        before.append((stored[0], stored[1]))
    if before:
        return max(before, key=lambda entry: entry[0])[1]

    after = [entry for entry in local if entry[0] > target]
    stored = (await session.execute(
        select(FxRate.date, FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date > target,
        )
        .order_by(FxRate.date.asc())
        .limit(1)
    )).first()
    if stored is not None:
        after.append((stored[0], stored[1]))
    return min(after, key=lambda entry: entry[0])[1] if after else None


async def has_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> bool:
    """Whether a real FX rate can be resolved between two currencies.

    Same resolution chain as :func:`get_rate` (exact date, on-demand fetch,
    closest stored date), but reports availability instead of falling back
    to 1:1. Callers use this to tell a genuine conversion apart from an
    unresolvable one (issue #574).
    """
    rate = await _resolve_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    return rate is not None


async def convert_or_none(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
    decimals: int = 2,
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Convert an amount, or return (None, None) when no rate can be resolved.

    Same precision rules as :func:`convert`, but never falls back to 1:1.
    Callers keep the primary-currency value NULL instead of displaying a
    fake amount, so mixed-currency totals never silently sum raw foreign
    values (issue #574).
    """
    if from_currency == to_currency:
        return amount, Decimal("1")

    rate = await _resolve_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    if rate is None:
        return None, None
    converted = amount * rate
    return converted.quantize(Decimal(1).scaleb(-decimals)), rate


async def convert(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
    decimals: int = 2,
) -> tuple[Decimal, Decimal]:
    """Convert an amount from one currency to another.

    Returns (converted_amount, rate_used). Uses the 1:1 fallback from
    :func:`get_rate` when no rate is available, so this is for live reads, not
    for persisting a stored conversion.

    ``decimals`` controls the precision of the converted amount. Monetary
    amounts keep the two-decimal default; asset values pass the price-field
    precision (``ASSET_PRICE_DECIMALS``) so a sub-cent market price survives
    the conversion instead of rounding to 0.00 (issue #622).
    """
    if from_currency == to_currency:
        return amount, Decimal("1")

    rate = await get_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    converted = amount * rate
    return converted.quantize(Decimal(1).scaleb(-decimals)), rate


async def stamp_primary_amount(
    session: AsyncSession,
    user_id: uuid.UUID,
    obj,
    amount_field: str = "amount",
    primary_field: str = "amount_primary",
    rate_field: str = "fx_rate_used",
    date_field: str = "date",
    currency_field: str = "currency",
    *,
    allow_fetch: bool = True,
    persist_fetched_rates: bool = True,
    fetched_rates: Optional[FetchedRateCache] = None,
) -> None:
    """Set obj's primary amount and fx_rate_used based on user's primary currency.

    Works for Transaction, RecurringTransaction, etc.

    When the object is in a foreign currency and no real FX rate is available,
    the fields are left untouched instead of persisting a fake 1:1 conversion
    (issue #353). A brand-new object therefore stays NULL (honest "not converted",
    reads fall back to the native amount via ``COALESCE(amount_primary, amount)``),
    while an already-stamped row keeps its current value — so re-stamping never
    pushes a visible transaction into limbo. The row heals on a later pass once a
    rate for its date lands. Pass ``allow_fetch=False`` to avoid the on-demand
    provider call (used by the healer to stay frugal). A caller that owns a larger
    transaction can pass ``persist_fetched_rates=False`` plus an import-local
    cache so fetching rates neither commits its data nor locks shared FX rows.
    """
    user = await session.get(User, user_id)
    if not user:
        return

    primary_currency = user.primary_currency
    obj_currency = getattr(obj, currency_field, get_settings().default_currency)
    obj_amount = getattr(obj, amount_field, None)

    if obj_amount is None:
        return

    amount_dec = Decimal(str(obj_amount))

    # Genuine same-currency 1:1 — always safe to persist.
    if obj_currency == primary_currency:
        setattr(obj, primary_field, amount_dec.quantize(Decimal("0.01")))
        if hasattr(obj, rate_field):
            setattr(obj, rate_field, Decimal("1"))
        return

    obj_date = getattr(obj, date_field, None)
    rate = await _resolve_rate(
        session, obj_currency, primary_currency, obj_date, allow_fetch=allow_fetch,
        persist_fetched_rates=persist_fetched_rates, fetched_rates=fetched_rates,
    )

    if rate is None:
        # No real rate available yet. Leave the fields untouched: a brand-new
        # object stays NULL (honest "not converted"), an existing row keeps its
        # current value. Either way we never persist a fake 1:1, and never push a
        # visible transaction into limbo. It heals on a later pass once a rate lands.
        return

    setattr(obj, primary_field, (amount_dec * rate).quantize(Decimal("0.01")))
    if hasattr(obj, rate_field):
        setattr(obj, rate_field, rate)
