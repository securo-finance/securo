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
