"""The bounds a value written to a money column has to respect (ADR 0008).

`amount`, `amount_primary` and `amount_account` are `Numeric(15, 2)`, so the
column rounds to cents and holds 13 digits at most; `fx_rate_used` is
`Numeric(20, 10)`. Without a bound in front of them, `0.001` was accepted and
stored as 0.00, and `1e15` reached asyncpg and came back as a raw database
error, both through the API and through the MCP tools (issue #270). Every
caller validates with these helpers so the two paths keep one rule.
"""
from decimal import ROUND_HALF_UP, Decimal

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
