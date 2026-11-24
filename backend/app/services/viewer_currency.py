"""Read stored primary amounts in the currency of the person looking at them.

`amount_primary` is stamped in the display currency of the row's owner
(`stamp_primary_amount` reads `tx.user_id`), and the owner's rows are
restamped when they switch currency. In a shared workspace the members can
display different currencies, so the stamp of a row another member created
is in theirs, not the viewer's: adding it as it is shows R$ 1.600 as
1.600 €. Every read that sums or shows `amount_primary` to a viewer goes
through a `ViewerCurrency`, which converts the rows of owners with another
currency at the row's date and leaves every other row as stored.

The SQL expression and the per-row conversion use the same rates (the
stored USD rates closest to the row's date, crossed through USD) and the
same rounding, so a total and the rows it drills into agree to the cent.

A row already in the viewer's currency reads as its own `amount`: that is
what was charged in that currency. Converting R$ 10,03 to the owner's euros
and back gave R$ 10,06, and a US$ 300 row with a typed R$ 1.560 read
US$ 312 to a viewer in dollars (#463). A typed conversion is the truth in
the owner's currency only.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.currency import normalize_currency_code
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User

_CENT = Decimal("0.01")


def _code(currency: Optional[str]) -> str:
    return normalize_currency_code(currency) or get_settings().default_currency


def _usd_rate_on(currency: str, on_date):
    """SQL: the stored USD→currency rate closest to `on_date`, earlier first."""
    if currency == "USD":
        return literal(1)
    pair = (FxRate.base_currency == "USD", FxRate.quote_currency == currency)
    before = (
        select(FxRate.rate)
        .where(*pair, FxRate.date <= on_date)
        .order_by(FxRate.date.desc())
        .limit(1)
        .scalar_subquery()
    )
    after = (
        select(FxRate.rate)
        .where(*pair, FxRate.date > on_date)
        .order_by(FxRate.date.asc())
        .limit(1)
        .scalar_subquery()
    )
    return func.coalesce(before, after)


@dataclass
class ViewerCurrency:
    """The viewer's display currency and the owners who stamp in another one."""

    currency: str
    # Stamp currency -> the owners whose rows are stamped in it.
    foreign_owners: dict[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    _ratios: dict[tuple[str, date], Decimal] = field(default_factory=dict, repr=False)

    def stamp_currency(self, owner_id: Optional[uuid.UUID]) -> str:
        for currency, owners in self.foreign_owners.items():
            if owner_id in owners:
                return currency
        return self.currency

    def amount(self, column=None, on_date=None):
        """SQL: `column` (a value stamped like `amount_primary`) in the
        viewer's currency. Rows of owners in the viewer's currency, and NULL
        stamps, come through unchanged."""
        same_currency_shortcut = column is None
        column = Transaction.amount_primary if column is None else column
        if not self.foreign_owners:
            return column
        on_date = Transaction.date if on_date is None else on_date
        target = _usd_rate_on(self.currency, on_date)
        whens = []
        if same_currency_shortcut:
            foreign = tuple(o for owners in self.foreign_owners.values() for o in owners)
            whens.append(
                (
                    and_(
                        # `convert()` normalizes the code; so does this.
                        func.upper(Transaction.currency) == self.currency,
                        Transaction.user_id.in_(foreign),
                        column.is_not(None),
                    ),
                    Transaction.amount,
                )
            )
        for currency, owners in self.foreign_owners.items():
            source = _usd_rate_on(currency, on_date)
            # A pair with no stored rate reads 1:1, like `fx_rate_service.convert`.
            ratio = func.coalesce(target / func.nullif(source, 0), 1)
            whens.append(
                (Transaction.user_id.in_(owners), func.round(column * ratio, 2))
            )
        return case(*whens, else_=column)

    async def ratio(
        self, session: AsyncSession, owner_id: Optional[uuid.UUID], on_date: date
    ) -> Decimal:
        """Stamp currency → viewer currency for one owner's row on a date."""
        source = self.stamp_currency(owner_id)
        if source == self.currency:
            return Decimal("1")
        key = (source, on_date)
        if key not in self._ratios:
            target_rate = await _usd_rate(session, self.currency, on_date)
            source_rate = await _usd_rate(session, source, on_date)
            if target_rate is None or not source_rate:
                self._ratios[key] = Decimal("1")
            else:
                self._ratios[key] = Decimal(str(target_rate)) / Decimal(str(source_rate))
        return self._ratios[key]

    async def has_rate(
        self, session: AsyncSession, owner_id: Optional[uuid.UUID], on_date: date
    ) -> bool:
        """Whether a stored rate links the owner's stamp currency to the
        viewer's on a date; without one, `ratio` falls back to 1."""
        source = self.stamp_currency(owner_id)
        if source == self.currency:
            return True
        target_rate = await _usd_rate(session, self.currency, on_date)
        source_rate = await _usd_rate(session, source, on_date)
        return target_rate is not None and bool(source_rate)

    async def convert(
        self,
        session: AsyncSession,
        owner_id: Optional[uuid.UUID],
        value,
        on_date: date,
        *,
        currency: Optional[str] = None,
        amount=None,
    ) -> Optional[Decimal]:
        """One stamped value of `owner_id`'s row, in the viewer's currency.

        Pass the row's `currency` and `amount` with its `amount_primary`: a
        row already in the viewer's currency then reads as its own amount,
        like `amount()` does in SQL (see the module docstring)."""
        if value is None:
            return None
        value = Decimal(str(value))
        if self.stamp_currency(owner_id) == self.currency:
            return value
        if (
            amount is not None
            and currency is not None
            and _code(currency) == self.currency
        ):
            return Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
        ratio = await self.ratio(session, owner_id, on_date)
        return (value * ratio).quantize(_CENT, rounding=ROUND_HALF_UP)


async def _usd_rate(session: AsyncSession, currency: str, on_date: date) -> Optional[Decimal]:
    if currency == "USD":
        return Decimal("1")
    return await session.scalar(select(_usd_rate_on(currency, on_date)))


async def viewer_currency(
    session: AsyncSession, currency: Optional[str]
) -> ViewerCurrency:
    """Build the conversion for a viewer who displays `currency`.

    Every user who displays another currency is listed: the rows a viewer
    reaches (a shared workspace, a group share from someone else's
    workspace, a former member's history) can belong to any of them, and a
    self-hosted instance has few users.
    """
    currency = _code(currency)
    owners: dict[str, list[uuid.UUID]] = {}
    for user_id, prefs in (await session.execute(select(User.id, User.preferences))).all():
        stamp = _code((prefs or {}).get("currency_display"))
        if stamp != currency:
            owners.setdefault(stamp, []).append(user_id)
    return ViewerCurrency(
        currency=currency,
        foreign_owners={code: tuple(ids) for code, ids in owners.items()},
    )
