import logging
import uuid
from bisect import bisect_left, bisect_right
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple, Optional

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.core.currency import same_currency
from app.core.money import MoneyOutOfRange, bounded_conversion
from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.openexchangerates import (
    FxProviderNotConfigured,
    FxProviderQuotaExceeded,
    OpenExchangeRatesProvider,
)
from app.services.provider_settings import resolve_settings

logger = logging.getLogger(__name__)

# On-demand fetch discipline (issue #624). A date the provider failed for is
# remembered for a while, so conversions that keep hitting the same missing day
# do not retry it; a 429 pauses every on-demand fetch, since the next call
# would fail the same way. The memory lives in the process: what it avoids is
# the repeated outbound request, and each process makes its own.
_FAILURE_TTL = timedelta(hours=6)
_QUOTA_COOLDOWN = timedelta(hours=1)
# Rates for dates older than this are sampled once a month: the first date of
# a month fetches the whole day's rates and the rest of the month reads the
# closest stored one. Importing years of rows then costs O(months), not
# O(days).
_MONTHLY_SAMPLING_AFTER = timedelta(days=31)

_failed_dates: dict[date, datetime] = {}
_quota_blocked_until: Optional[datetime] = None
_quota_warning_at: Optional[datetime] = None

_stored_only: ContextVar[bool] = ContextVar("fx_stored_rates_only", default=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset_fx_failure_cache() -> None:
    """Forget remembered provider failures.

    For tests and for an explicit refresh, which is the moment a person asks
    to try the provider again.
    """
    global _quota_blocked_until, _quota_warning_at
    _failed_dates.clear()
    _quota_blocked_until = None
    _quota_warning_at = None


def _remember_failure(target: date) -> None:
    now = _now()
    _failed_dates[target] = now + _FAILURE_TTL
    # Drop the ones that already expired, so the dict stays bounded.
    for failed, until in list(_failed_dates.items()):
        if until <= now:
            del _failed_dates[failed]


def _fetch_blocked(target: date) -> bool:
    """Whether the provider must not be called for this date right now."""
    now = _now()
    if _quota_blocked_until is not None and now < _quota_blocked_until:
        return True
    until = _failed_dates.get(target)
    if until is None:
        return False
    if now < until:
        return True
    del _failed_dates[target]
    return False


def remember_quota_exceeded(target: date) -> None:
    """Back off every on-demand fetch and warn once per cooldown (issue #624).

    A spent quota is not a bug in the request, so it is said in one line
    without a traceback, not once per date tried. Both the on-demand path and
    the manual refresh use it: a 429 is the same fact wherever it is seen.
    """
    global _quota_blocked_until, _quota_warning_at
    now = _now()
    _remember_failure(target)
    _quota_blocked_until = now + _QUOTA_COOLDOWN
    if _quota_warning_at is None or now - _quota_warning_at >= _QUOTA_COOLDOWN:
        _quota_warning_at = now
        logger.warning(
            "FX provider quota exceeded (HTTP 429); pausing on-demand rate "
            "fetches for %d minutes",
            int(_QUOTA_COOLDOWN.total_seconds() // 60),
        )


def _samples_monthly(target: date) -> bool:
    """Whether a date is old enough to read the month's stored rate instead."""
    return target < app_today() - _MONTHLY_SAMPLING_AFTER


async def _month_has_stored_rate(
    session: AsyncSession, currency: str, target: date
) -> bool:
    first = target.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    found = await session.scalar(
        select(FxRate.id)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date >= first,
            FxRate.date < next_first,
        )
        .limit(1)
    )
    return found is not None


async def _may_fetch(
    session: AsyncSession, target: date, currencies: tuple[str, str]
) -> bool:
    """Whether an on-demand provider call is worth making for this date."""
    if _fetch_blocked(target):
        return False
    if _samples_monthly(target):
        # Both legs need the month's sample: one leg's stored rate must not
        # suppress the fetch the other leg still needs (the month may only
        # have history for one of them).
        legs = [currency for currency in currencies if currency != "USD"]
        sampled = [
            await _month_has_stored_rate(session, currency, target)
            for currency in legs
        ]
        if legs and all(sampled):
            logger.debug(
                "Using the month's stored FX rates for %s instead of fetching", target
            )
            return False
    return True


@contextmanager
def stored_rates_only() -> Generator[None]:
    """Convert with the rates already stored, whatever the callee asks for.

    For unattended reads through services that fetch on demand: nobody asked
    for a rate, so no call leaves the instance (ADR 0016)."""
    token = _stored_only.set(True)
    try:
        yield
    finally:
        _stored_only.reset(token)


def _code(currency: str) -> str:
    """A currency code as rates are stored: no spaces, upper case (#1474)."""
    return currency.strip().upper()


async def sync_rates(
    session: AsyncSession, target_date: Optional[date] = None
) -> int:
    """Fetch rates from the provider for the given date and upsert into fx_rates.

    Only saves rates for currencies in `supported_currencies`.
    Idempotent — existing rates for the same date are updated.
    Returns the number of rates synced.
    """
    provider = OpenExchangeRatesProvider(await resolve_settings(session))
    requested_target = target_date or app_today()
    # Providers cannot return a historical rate for a date that has not
    # happened yet. Treat a future request as a request for today's latest
    # published rate and store it under today's date.
    target = min(requested_target, app_today())
    supported = {_code(c) for c in get_settings().supported_currencies.split(",") if c.strip()}

    rates = await _fetch(provider, target)

    count = 0
    for currency_code, rate in rates.items():
        currency_code = _code(currency_code)
        if currency_code not in supported:
            continue
        stmt = pg_insert(FxRate).values(
            base_currency="USD",
            quote_currency=currency_code,
            date=target,
            rate=rate,
            source=provider.name,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fx_rate_base_quote_date",
            set_={"rate": rate, "source": provider.name},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d FX rates for %s", count, target)
    return count


async def _lock_day(session: AsyncSession, target: date) -> None:
    """One provider fetch per day at a time, until the transaction ends.

    Two administrator refreshes of the same day would otherwise both count no
    stored rate before either commits, and both spend a provider call: the app
    id is the instance's, so that is twice the shared quota for one day
    (issue #636). PostgreSQL only, in the same shape as the import lock in
    `asset_import_service.py`: SQLite, which the tests use, serialises writers
    itself.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"fx-refresh:{target}")))
    )


async def sync_rates_for_refresh(
    session: AsyncSession, target: date
) -> tuple[int, bool]:
    """Fetch a day's rates unless the instance already stored that day.

    The manual refresh spends one provider call per day at most (issue #636):
    the app id is the instance's, so asking again for a day that is already
    stored must not cost the shared quota. Returns the number of stored or
    synced rates and whether the provider was skipped.
    """
    await _lock_day(session, target)
    stored = await session.scalar(
        select(func.count()).select_from(FxRate).where(FxRate.date == target)
    ) or 0
    if stored:
        return stored, True
    return await sync_rates(session, target), False


async def _fetch(provider: OpenExchangeRatesProvider, target: date) -> dict[str, Decimal]:
    if target == app_today():
        return await provider.fetch_latest()
    return await provider.fetch_historical(target)


async def fetch_rates(session: AsyncSession, target_date: date) -> dict[str, Decimal]:
    """The provider's USD-based rates for a day, without storing them.

    For a caller that must not write, such as an import preview. Raises when
    the provider is not configured or the request fails, like
    :func:`sync_rates`. A future date reads today's latest rates.
    """
    provider = OpenExchangeRatesProvider(await resolve_settings(session))
    return await _fetch(provider, min(target_date, app_today()))


async def _resolve_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> Optional[Decimal]:
    """Resolve the true FX rate, or None when no rate can be found.

    Uses cross-rate through USD: rate = usd_to_target / usd_to_source.
    Priority: exact date → on-demand fetch (when the provider is configured)
    → closest available.
    Returns None (not a fake 1:1) when no rate is available, so callers that
    persist a conversion can honestly leave it NULL instead of storing a wrong
    amount (issue #353). Pass ``allow_fetch=False`` to skip the on-demand
    provider call and rely only on already-stored rates.

    The codes are compared as stored (``_code``): an account can keep the code
    as it was typed, and a literal "rsd" used to miss its rate (#1474).
    """
    from_currency, to_currency = _code(from_currency), _code(to_currency)
    if from_currency == to_currency:
        return Decimal("1")

    requested_target = target_date or app_today()
    # A future transaction must use the latest real rate, never query a
    # not-yet-existing historical snapshot. Looking up against today also
    # lets the closest-rate fallback select the last cached business day.
    target = min(requested_target, app_today())

    # Step 1: Try exact date
    usd_to_source = await _get_exact_date_rate(session, from_currency, target)
    usd_to_target = await _get_exact_date_rate(session, to_currency, target)

    # Step 2: If missing, fetch from provider for exact date
    if (
        allow_fetch
        and not _stored_only.get()
        and (usd_to_source is None or usd_to_target is None)
        and await _may_fetch(session, target, (from_currency, to_currency))
    ):
        try:
            synced = await sync_rates(session, target)
            _failed_dates.pop(target, None)
            if synced > 0:
                logger.info("On-demand sync fetched %d rates for %s", synced, target)
                if usd_to_source is None:
                    usd_to_source = await _get_exact_date_rate(session, from_currency, target)
                if usd_to_target is None:
                    usd_to_target = await _get_exact_date_rate(session, to_currency, target)
        except FxProviderNotConfigured:
            # An instance with no app id is configured that way on purpose
            # (ADR 0004). Stay on the stored rates without a warning or a
            # traceback for every conversion (issue #267).
            logger.debug("FX provider not configured; using stored rates for %s", target)
        except FxProviderQuotaExceeded:
            remember_quota_exceeded(target)
        except Exception:
            _remember_failure(target)
            logger.warning("On-demand FX rate sync failed for %s", target, exc_info=True)

    # Step 3: Fall back to closest available date
    if usd_to_source is None:
        usd_to_source = await _get_closest_rate(session, from_currency, target)
    if usd_to_target is None:
        usd_to_target = await _get_closest_rate(session, to_currency, target)

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


async def _get_exact_date_rate(session: AsyncSession, currency: str, target: date) -> Optional[Decimal]:
    """Get the rate for an exact date."""
    if currency == "USD":
        return Decimal("1")
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date == target,
        )
    )
    return result


async def _get_closest_rate(session: AsyncSession, currency: str, target: date) -> Optional[Decimal]:
    """Get the closest available rate to a target date (preferring before, then after)."""
    if currency == "USD":
        return Decimal("1")
    # Try closest before or on target date
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date <= target,
        )
        .order_by(desc(FxRate.date))
        .limit(1)
    )
    if result is not None:
        return result
    # Try closest after target date
    from sqlalchemy import asc
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date > target,
        )
        .order_by(asc(FxRate.date))
        .limit(1)
    )
    return result


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


class RateBook:
    """Today's rates for one read, resolved once per currency pair.

    A chart that converts many rows at the same date used to resolve the same
    rate again for each row: two ``fx_rates`` lookups and one ``app_settings``
    read every time, plus an on-demand provider attempt. The book resolves a
    pair once and answers the rest from memory, so the request costs one
    lookup per currency instead of one per row (issue #582).

    For reads only, where every row shares the target date and the
    ``allow_fetch`` setting. A write that stamps rows keeps calling
    :func:`convert` directly.
    """

    def __init__(
        self,
        session: AsyncSession,
        target_date: Optional[date] = None,
        *,
        allow_fetch: bool = True,
    ) -> None:
        self._session = session
        self._target_date = target_date
        self._allow_fetch = allow_fetch
        self._rates: dict[tuple[str, str], Decimal] = {}

    async def rate(self, from_currency: str, to_currency: str) -> Decimal:
        key = (_code(from_currency), _code(to_currency))
        if key not in self._rates:
            self._rates[key] = await get_rate(
                self._session,
                key[0],
                key[1],
                self._target_date,
                allow_fetch=self._allow_fetch,
            )
        return self._rates[key]

    async def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        *,
        decimals: int = 2,
    ) -> tuple[Decimal, Decimal]:
        if from_currency == to_currency:
            return amount, Decimal("1")
        rate = await self.rate(from_currency, to_currency)
        return (amount * rate).quantize(Decimal(1).scaleb(-decimals)), rate


class DatedRateBook:
    """Rates on many dates for one read, from one query per currency.

    A chart over years of history converted each point with :func:`convert`,
    which reads ``fx_rates`` two to six times per date: about 9,500 queries
    for one portfolio trend. The book loads each currency's USD series once
    and resolves a date in memory with the same chain as
    :func:`_resolve_rate` (exact date, then closest before, then closest
    after). A date that would trigger an on-demand provider fetch still goes
    through :func:`get_rate`, so that path keeps its behavior and its guards.
    When such a fetch stores nothing for its date (no provider configured, a
    spent quota, a failed call), the book stops asking: the next dates resolve
    in memory from the stored series instead of repeating the fetch, the
    settings read and the closest-rate queries once per date.

    For reads only, like :class:`RateBook`.
    """

    def __init__(self, session: AsyncSession, *, allow_fetch: bool = True) -> None:
        self._session = session
        self._allow_fetch = allow_fetch
        self._series: dict[str, tuple[list[date], list[Decimal]]] = {}
        self._rates: dict[tuple[str, str, date], Decimal] = {}
        self._warned: set[tuple[str, str]] = set()
        self._fetch_exhausted = False

    async def _load(self, currency: str) -> tuple[list[date], list[Decimal]]:
        series = self._series.get(currency)
        if series is None:
            rows = (
                await self._session.execute(
                    select(FxRate.date, FxRate.rate)
                    .where(FxRate.base_currency == "USD", FxRate.quote_currency == currency)
                    .order_by(FxRate.date)
                )
            ).all()
            series = ([row[0] for row in rows], [row[1] for row in rows])
            self._series[currency] = series
        return series

    @staticmethod
    def _exact(series: tuple[list[date], list[Decimal]], target: date) -> Optional[Decimal]:
        dates, rates = series
        i = bisect_left(dates, target)
        return rates[i] if i < len(dates) and dates[i] == target else None

    @staticmethod
    def _closest(series: tuple[list[date], list[Decimal]], target: date) -> Optional[Decimal]:
        dates, rates = series
        i = bisect_right(dates, target)
        if i > 0:
            return rates[i - 1]
        return rates[0] if rates else None

    @staticmethod
    def _month_has(series: tuple[list[date], list[Decimal]], target: date) -> bool:
        dates, _ = series
        first = target.replace(day=1)
        next_first = (first + timedelta(days=32)).replace(day=1)
        i = bisect_left(dates, first)
        return i < len(dates) and dates[i] < next_first

    def _may_fetch(self, target: date, legs: dict[str, tuple[list[date], list[Decimal]]]) -> bool:
        if (
            not self._allow_fetch
            or self._fetch_exhausted
            or _stored_only.get()
            or _fetch_blocked(target)
        ):
            return False
        if _samples_monthly(target) and legs and all(
            self._month_has(series, target) for series in legs.values()
        ):
            return False
        return True

    async def _date_has_rates(self, target: date) -> bool:
        found = await self._session.scalar(
            select(FxRate.id).where(FxRate.date == target).limit(1)
        )
        return found is not None

    async def rate(self, from_currency: str, to_currency: str, target_date: date) -> Decimal:
        source, quote = _code(from_currency), _code(to_currency)
        if source == quote:
            return Decimal("1")
        target = min(target_date, app_today())
        key = (source, quote, target)
        cached = self._rates.get(key)
        if cached is not None:
            return cached

        legs = {c: await self._load(c) for c in (source, quote) if c != "USD"}
        exact = {c: self._exact(series, target) for c, series in legs.items()}
        if any(value is None for value in exact.values()) and self._may_fetch(target, legs):
            rate = await get_rate(
                self._session, source, quote, target, allow_fetch=self._allow_fetch
            )
            # The fetch may have stored this date's rows: keep the series current.
            stored_any = False
            for currency, series in legs.items():
                if exact[currency] is None:
                    stored = await _get_exact_date_rate(self._session, currency, target)
                    if stored is not None:
                        stored_any = True
                        i = bisect_left(series[0], target)
                        series[0].insert(i, target)
                        series[1].insert(i, stored)
            if not stored_any and not await self._date_has_rates(target):
                # Nothing came back for the day: the provider is off, out of
                # quota or failing. Asking again for every later date would
                # give the same answer at the cost of a fetch attempt each.
                self._fetch_exhausted = True
            self._rates[key] = rate
            return rate

        usd = {
            c: exact[c] if exact[c] is not None else self._closest(series, target)
            for c, series in legs.items()
        }
        usd_to_source = usd.get(source, Decimal("1"))
        usd_to_target = usd.get(quote, Decimal("1"))
        if usd_to_source is None or usd_to_target is None or usd_to_source == 0:
            if (source, quote) not in self._warned:
                self._warned.add((source, quote))
                logger.warning(
                    "No FX rate found for %s -> %s on %s, returning 1", source, quote, target
                )
            rate = Decimal("1")
        else:
            rate = usd_to_target / usd_to_source
        self._rates[key] = rate
        return rate

    async def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        target_date: date,
        *,
        decimals: int = 2,
    ) -> tuple[Decimal, Decimal]:
        if from_currency == to_currency:
            return amount, Decimal("1")
        rate = await self.rate(from_currency, to_currency, target_date)
        return (amount * rate).quantize(Decimal(1).scaleb(-decimals)), rate


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
    account_amount: Optional[Decimal] = None,
    restamp_account: bool = False,
) -> None:
    """Stamp obj's primary amount and, for a transaction, its account amount.

    `account_amount` is the figure the bank charged the account, when the
    provider reports one. Pass `restamp_account` when the row's amount,
    currency, date or account changed; a stamp that only heals the reporting
    value keeps the account amount (see `stamp_account_amount`).
    """
    before = (getattr(obj, primary_field, None), getattr(obj, rate_field, None))
    await _stamp_primary_fields(
        session, user_id, obj, amount_field, primary_field, rate_field,
        date_field, currency_field, allow_fetch=allow_fetch,
    )
    if isinstance(obj, Transaction) and primary_field == "amount_primary":
        try:
            await stamp_account_amount(
                session,
                obj,
                bank_amount=account_amount,
                allow_fetch=allow_fetch,
                recompute=restamp_account,
            )
        except MoneyOutOfRange:
            # The account leg converts past the column (issue #422): put the
            # primary stamp back, so the row is left as it was instead of
            # half stamped and out of the healers' reach.
            setattr(obj, primary_field, before[0])
            setattr(obj, rate_field, before[1])
            raise


# Where a stored `amount_account` came from. NULL is a derived value: a copy of
# `amount_primary` or a conversion at a stored rate, safe to work out again.
ACCOUNT_AMOUNT_FROM_BANK = "bank"
ACCOUNT_AMOUNT_FROM_USER = "user"


async def stamp_account_amount(
    session: AsyncSession,
    transaction: Transaction,
    *,
    bank_amount: Optional[Decimal] = None,
    typed_primary: bool = False,
    allow_fetch: bool = True,
    recompute: bool = True,
) -> None:
    """Set `amount_account`: the row's value in its account's currency (ADR 0027).

    Balances and bills read it for a row in another currency than its account,
    so it must not follow the owner's display currency the way
    `amount_primary` does. In order of preference:

    - the amount the bank charged the account, when the provider reports it;
    - `amount_primary`, when the account is in the owner's primary currency,
      since the two are then the same figure. `typed_primary` says the user
      typed that amount in the transaction dialog;
    - the stored rate from the row's currency into the account's, for its date.

    With none of these the field is left as it is, like `amount_primary`.
    A row in its account's own currency gets NULL; readers use `amount`.

    `amount_account_source` records which one it was. Without `recompute` (a
    stamp that only heals the reporting value: posting a pending row, the FX
    healers) a value from the bank or the user is kept; only a derived one is
    worked out again.
    """
    account = await session.get(Account, transaction.account_id)
    if account is None:
        return
    if same_currency(transaction.currency, account.currency):
        transaction.amount_account = None
        transaction.amount_account_source = None
        return
    if bank_amount is not None:
        transaction.amount_account = Decimal(str(bank_amount)).quantize(Decimal("0.01"))
        transaction.amount_account_source = ACCOUNT_AMOUNT_FROM_BANK
        return
    if (
        not recompute
        and transaction.amount_account is not None
        and transaction.amount_account_source is not None
    ):
        return
    user = await session.get(User, transaction.user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    if same_currency(account.currency, primary):
        if transaction.amount_primary is not None:
            transaction.amount_account = transaction.amount_primary
            transaction.amount_account_source = (
                ACCOUNT_AMOUNT_FROM_USER if typed_primary else None
            )
        return
    if transaction.amount is None:
        return
    rate = await _resolve_rate(
        session, transaction.currency, account.currency, transaction.date,
        allow_fetch=allow_fetch,
    )
    if rate is not None:
        transaction.amount_account = bounded_conversion(
            (Decimal(str(transaction.amount)) * rate).quantize(Decimal("0.01")),
            "amount_account",
        )
        transaction.amount_account_source = None


class PrimaryStamp(NamedTuple):
    """A restamp's result, as a preview shows it.

    ``rate_is_exact_date`` says whether the rate is the row date's own, from
    stored data. When it is False the rate came from the closest stored date,
    so a save that may fetch can still land on a different exact-date rate and
    the preview must be read as an estimate.
    """

    amount_primary: Optional[Decimal]
    fx_rate_used: Optional[Decimal]
    rate_is_exact_date: bool


async def resolve_primary_stamp(
    session: AsyncSession,
    primary_currency: str,
    amount: Decimal,
    currency: str,
    on_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> PrimaryStamp:
    """What a restamp writes for this amount, without writing it.

    Returns the ``amount_primary`` and ``fx_rate_used`` exactly as
    :func:`_stamp_primary_fields` would set them, or both ``None`` when the
    row is in a foreign currency and no real rate can be resolved for its
    date. A preview uses it so it can show the conversion a save will make
    instead of guessing (issue #271).

    Raises ``MoneyOutOfRange`` when the conversion does not fit the column
    (issue #422).
    """
    amount_dec = Decimal(str(amount))

    # Genuine same-currency 1:1, always safe to persist.
    if _code(currency) == _code(primary_currency):
        return PrimaryStamp(amount_dec.quantize(Decimal("0.01")), Decimal("1"), True)

    rate = await _resolve_rate(
        session, currency, primary_currency, on_date, allow_fetch=allow_fetch
    )
    if rate is None:
        return PrimaryStamp(None, None, False)

    # The same pair _resolve_rate works with: a rate is exact only when both
    # legs are stored for the row's own date. Checked after the resolve so an
    # allowed fetch counts, and after clamping future dates to today the way
    # _resolve_rate does.
    from_currency, to_currency = _code(currency), _code(primary_currency)
    target = min(on_date or app_today(), app_today())
    legs = [c for c in (from_currency, to_currency) if c != "USD"]
    exact = True
    for leg in legs:
        if await _get_exact_date_rate(session, leg, target) is None:
            exact = False
            break
    return PrimaryStamp(
        bounded_conversion((amount_dec * rate).quantize(Decimal("0.01"))), rate, exact
    )


async def _stamp_primary_fields(
    session: AsyncSession,
    user_id: uuid.UUID,
    obj,
    amount_field: str,
    primary_field: str,
    rate_field: str,
    date_field: str,
    currency_field: str,
    *,
    allow_fetch: bool,
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
    provider call (used by the healer to stay frugal).
    """
    user = await session.get(User, user_id)
    if not user:
        return

    obj_amount = getattr(obj, amount_field, None)
    if obj_amount is None:
        return

    stamp = await resolve_primary_stamp(
        session,
        user.primary_currency,
        obj_amount,
        getattr(obj, currency_field, get_settings().default_currency),
        getattr(obj, date_field, None),
        allow_fetch=allow_fetch,
    )

    if stamp.amount_primary is None:
        # No real rate available yet. Leave the fields untouched: a brand-new
        # object stays NULL (honest "not converted"), an existing row keeps its
        # current value. Either way we never persist a fake 1:1, and never push a
        # visible transaction into limbo. It heals on a later pass once a rate lands.
        return

    setattr(obj, primary_field, stamp.amount_primary)
    if hasattr(obj, rate_field):
        setattr(obj, rate_field, stamp.fx_rate_used)
