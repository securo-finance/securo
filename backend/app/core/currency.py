"""Normalization for ISO 4217 currency codes.

A code is stored upper-case. Rows and accounts written before the schemas
normalized on write can still hold a lower-case code (OFX carries its own
``Ccy`` and the provider sync stores what the adapter returned), so the
comparisons in the balance helpers normalize both sides instead of assuming
they agree.
"""
from typing import Optional


def normalize_currency_code(code: Optional[str]) -> Optional[str]:
    """Upper-case a currency code, leaving ``None`` alone."""
    return code.upper() if isinstance(code, str) else code


def same_currency(left: Optional[str], right: Optional[str]) -> bool:
    """Whether two codes name the same currency, ignoring their case."""
    return normalize_currency_code(left) == normalize_currency_code(right)
