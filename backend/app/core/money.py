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


def bounded_money(value: Decimal, field: str = "amount") -> Decimal:
    """`value` as the column stores it, or a readable error.

    Returns the amount rounded to cents, so the caller saves exactly what was
    validated. A value that rounds to zero is refused instead of being written
    as 0.00, and one past the column is refused instead of failing at commit.
    """
    _finite(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be greater than zero")
    if value > MAX_MONEY_AMOUNT:
        raise ValueError(f"{field} must be at most {MAX_MONEY_AMOUNT}")
    rounded = value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError(f"{field} must be at least 0.01")
    return rounded


def bounded_fx_rate(value: Decimal, field: str = "fx_rate_used") -> Decimal:
    """`value` as the rate column stores it, or a readable error."""
    _finite(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be greater than zero")
    if value > MAX_FX_RATE:
        raise ValueError(f"{field} must be at most {MAX_FX_RATE}")
    rounded = value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError(f"{field} must be at least 0.0000000001")
    return rounded
