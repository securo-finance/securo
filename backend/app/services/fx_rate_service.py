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
from app.core.currency import same_currency
from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.openexchangerates import OpenExchangeRatesProvider
from app.services.provider_settings import resolve_settings

logger = logging.getLogger(__name__)


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
    Priority: exact date → on-demand fetch → closest available.
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
    if allow_fetch and (usd_to_source is None or usd_to_target is None):
        try:
            synced = await sync_rates(session, target)
            if synced > 0:
                logger.info("On-demand sync fetched %d rates for %s", synced, target)
                if usd_to_source is None:
                    usd_to_source = await _get_exact_date_rate(session, from_currency, target)
                if usd_to_target is None:
                    usd_to_target = await _get_exact_date_rate(session, to_currency, target)
        except Exception:
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
    await _stamp_primary_fields(
        session, user_id, obj, amount_field, primary_field, rate_field,
        date_field, currency_field, allow_fetch=allow_fetch,
    )
    if isinstance(obj, Transaction) and primary_field == "amount_primary":
        await stamp_account_amount(
            session,
            obj,
            bank_amount=account_amount,
            allow_fetch=allow_fetch,
            recompute=restamp_account,
        )


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
        transaction.amount_account = (
            Decimal(str(transaction.amount)) * rate
        ).quantize(Decimal("0.01"))
        transaction.amount_account_source = None


async def resolve_primary_stamp(
    session: AsyncSession,
    primary_currency: str,
    amount: Decimal,
    currency: str,
    on_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """What a restamp writes for this amount, without writing it.

    Returns ``(amount_primary, fx_rate_used)`` exactly as
    :func:`_stamp_primary_fields` would set them, or ``(None, None)`` when the
    row is in a foreign currency and no real rate can be resolved for its
    date. A preview uses it so it can show the conversion a save will make
    instead of guessing (issue #271).
    """
    amount_dec = Decimal(str(amount))

    # Genuine same-currency 1:1 — always safe to persist.
    if currency == primary_currency:
        return amount_dec.quantize(Decimal("0.01")), Decimal("1")

    rate = await _resolve_rate(
        session, currency, primary_currency, on_date, allow_fetch=allow_fetch
    )
    if rate is None:
        return None, None
    return (amount_dec * rate).quantize(Decimal("0.01")), rate


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

    primary, rate = await resolve_primary_stamp(
        session,
        user.primary_currency,
        obj_amount,
        getattr(obj, currency_field, get_settings().default_currency),
        getattr(obj, date_field, None),
        allow_fetch=allow_fetch,
    )

    if primary is None:
        # No real rate available yet. Leave the fields untouched: a brand-new
        # object stays NULL (honest "not converted"), an existing row keeps its
        # current value. Either way we never persist a fake 1:1, and never push a
        # visible transaction into limbo. It heals on a later pass once a rate lands.
        return

    setattr(obj, primary_field, primary)
    if hasattr(obj, rate_field):
        setattr(obj, rate_field, rate)
