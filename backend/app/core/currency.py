"""Normalization for ISO 4217 currency codes.

A code is stored upper-case. Rows and accounts written before the schemas
normalized on write can still hold a lower-case code (OFX carries its own
``Ccy`` and the provider sync stores what the adapter returned), so the
comparisons in the balance helpers normalize both sides instead of assuming
they agree.
"""
from typing import Optional

from app.core.config import get_settings


def normalize_currency_code(code: Optional[str]) -> Optional[str]:
    """Upper-case a currency code, leaving ``None`` alone."""
    return code.upper() if isinstance(code, str) else code


def same_currency(left: Optional[str], right: Optional[str]) -> bool:
    """Whether two codes name the same currency, ignoring their case."""
    return normalize_currency_code(left) == normalize_currency_code(right)


def supported_currency_codes() -> set[str]:
    """The codes this instance offers, from ``SUPPORTED_CURRENCIES``."""
    return {
        code.strip().upper()
        for code in get_settings().supported_currencies.split(",")
        if code.strip()
    }


def parse_supported_currency(code: str) -> str:
    """Trim and upper-case a code typed by a client, refusing one the
    instance does not support (``ValueError``, a 422 in a schema)."""
    normalized = code.strip().upper()
    if normalized not in supported_currency_codes():
        raise ValueError(f"unsupported currency code: {code.strip()!r}")
    return normalized


class UnsupportedCurrency(ValueError):
    """A transaction moved to a code the instance does not support. Routes
    answer it with 422, like a bad value in the request body."""


def check_transaction_currency(code: str, kept: Optional[str]) -> str:
    """The code a transaction is written with.

    A new code must be in ``SUPPORTED_CURRENCIES``. ``kept`` (the row's own
    currency on an edit, the account's on a create) is accepted as it is:
    a row synced or imported in another currency still saves when the form
    sends its currency back unchanged.
    """
    normalized = code.strip().upper()
    if same_currency(normalized, kept) or normalized in supported_currency_codes():
        return normalized
    raise UnsupportedCurrency(f"unsupported currency code: {code.strip()!r}")


def clean_currency_input(value: object, *, allow_null: bool) -> object:
    """Schema-side check of a currency a client sent: trimmed and upper-case,
    never empty, and null only where it means "the account's currency"."""
    if value is None:
        if allow_null:
            return None
        raise ValueError("currency cannot be null")
    if isinstance(value, str):
        value = value.strip().upper()
        if not value:
            raise ValueError("currency cannot be empty")
    return value
