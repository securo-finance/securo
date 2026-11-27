"""The bounds a value written to a money column has to respect (ADR 0008).

`amount`, `amount_primary` and `amount_account` are `Numeric(15, 2)`, so the
column rounds to cents and holds 13 digits at most; `fx_rate_used` is
`Numeric(20, 10)`. Without a bound in front of them, `0.001` was accepted and
stored as 0.00, and `1e15` reached asyncpg and came back as a raw database
error, both through the API and through the MCP tools (issue #270). Every
caller validates with these helpers so the two paths keep one rule.
"""
from decimal import ROUND_HALF_UP, Decimal
from functools import cache

# `Numeric(15, 2)`: 13 digits and two decimals on each side.
CENTS = Decimal("0.01")
MAX_MONEY_AMOUNT = Decimal("9999999999999.99")
# `Numeric(20, 10)` on `fx_rate_used`.
RATE_QUANTUM = Decimal("1E-10")
MAX_FX_RATE = Decimal("9999999999.9999999999")


def _finite(value: Decimal, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be a finite number")


class MoneyOutOfRange(ValueError):
    """A value worked out from other inputs (a converted amount, a derived
    rate) that the column cannot hold. Routes answer it with 422, like the
    same bound on a value the client sent."""


def as_stored_amount(value: Decimal) -> Decimal:
    """`value` as a money column stores it: to cents, half away from zero.

    For comparing an amount a file or a provider sent with one already
    stored. The comparison used to run in the database, which rounded the
    bound value to the column's scale before matching; made in Python, both
    sides have to be rounded here instead (issue #616).
    """
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def bounded_conversion(value: Decimal, field: str = "amount_primary") -> Decimal:
    """A conversion the backend worked out itself, as the column stores it.

    The rate comes from the stored FX table, not from the client, so a large
    amount in a weak currency can convert past the column even though the
    amount itself fits: 2e12 USD is about 1e13 BRL (issue #422). Refused with
    `MoneyOutOfRange` (a 422 on the routes) instead of failing at flush with a
    raw database error.
    Pass the value already rounded the way the caller stores it; it comes
    back unchanged. The sign is kept: callers stamp signed amounts too.
    """
    if abs(value) > MAX_MONEY_AMOUNT:
        raise MoneyOutOfRange(f"{field} must be at most {MAX_MONEY_AMOUNT}")
    return value


@cache
def max_asset_value() -> Decimal:
    """The largest value `asset_values.amount` holds, read from its column.

    The asset columns keep 6 decimals and have their own width — today
    `Numeric(15, 6)`, 9 integer digits, while the money columns keep 13. The
    bound is taken from the model instead of written down here so a migration
    that widens the column (the one in #1951 takes it to `Numeric(19, 6)`)
    moves it along, without an ordering between the two changes.
    """
    from sqlalchemy import Numeric

    from app.models.asset_value import AssetValue

    column = AssetValue.__table__.c.amount.type
    if not isinstance(column, Numeric) or column.precision is None or column.scale is None:
        raise TypeError("asset_values.amount must be Numeric with a fixed precision and scale")
    integer_digits = column.precision - column.scale
    return Decimal(10) ** integer_digits - Decimal(1).scaleb(-column.scale)


def bounded_asset_value(value: Decimal, field: str = "amount") -> Decimal:
    """A value worked out for `asset_values.amount`, or a readable refusal.

    The value is usually not the client's: a growth rule compounded for years,
    a provider balance in a weak currency, units × price. Any of them can go
    past the column while every input fits, and then the write fails at flush
    with a raw `NumericValueOutOfRangeError` — a 500 instead of an answer
    (issue #642). The ceiling is the column's own (`max_asset_value`), and
    `NaN` and `Infinity` are refused here too: the column can hold neither,
    and `NaN` cannot even be compared.
    """
    if not value.is_finite():
        raise MoneyOutOfRange(f"{field} must be a finite number")
    ceiling = max_asset_value()
    if abs(value) > ceiling:
        raise MoneyOutOfRange(f"{field} must be at most {ceiling}")
    return value


def bounded_money(
    value: Decimal, field: str = "amount", *, allow_zero: bool = False
) -> Decimal:
    """`value` as the column stores it, or a readable error.

    Returns the amount rounded to cents, so the caller saves exactly what was
    validated. A value that rounds to zero is refused instead of being written
    as 0.00, and one past the column is refused instead of failing at commit.

    `allow_zero` is for a converted amount (`amount_primary`): 0.01 BRL at a
    rate of 0.2 is 0.002 USD, which the column stores as 0.00. That is a
    real conversion, not a typo, so it is kept as 0.00 (issue #330).
    """
    _finite(value, field)
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{field} must be greater than zero")
    if value > MAX_MONEY_AMOUNT:
        raise ValueError(f"{field} must be at most {MAX_MONEY_AMOUNT}")
    rounded = value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if rounded <= 0 and not allow_zero:
        raise ValueError(f"{field} must be at least 0.01")
    return rounded


def bounded_fx_rate(
    value: Decimal, field: str = "fx_rate_used", *, allow_zero: bool = False
) -> Decimal:
    """`value` as the rate column stores it, or a readable error.

    `allow_zero` is for a rate derived from a converted amount of 0.00."""
    _finite(value, field)
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{field} must be greater than zero")
    if value > MAX_FX_RATE:
        raise ValueError(f"{field} must be at most {MAX_FX_RATE}")
    rounded = value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0 and not allow_zero:
        raise ValueError(f"{field} must be at least 0.0000000001")
    return rounded


def check_zero_rate(
    amount: Decimal | None, amount_primary: Decimal | None, fx_rate_used: Decimal | None
) -> None:
    """Refuse a rate of 0 unless it is what the pair itself rounds to.

    A stored rate of 0 comes from `amount_primary / amount` quantized to
    `RATE_QUANTUM`: a converted amount of 0.00 (issue #330), or one so small
    next to `amount` that the ratio is under 5e-11 (0.01 on 9999999999999.99).
    Sent back with the pair that produced it, it is accepted; next to a real
    conversion it is refused. Without `amount` the check waits for the caller
    that knows the row's amount.
    """
    if fx_rate_used is None or fx_rate_used != 0 or amount_primary == 0:
        return
    if amount_primary is not None:
        if amount is None:
            return
        if amount != 0:
            ratio = (amount_primary / amount).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
            if ratio == 0:
                return
    raise ValueError("fx_rate_used must be greater than zero")
