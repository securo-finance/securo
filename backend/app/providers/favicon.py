"""Derive a logo URL from a website/domain via Google's favicon service.

Shared by the market-price provider (asset logos from a company website) and
the bank providers (institution logos when an integration exposes the bank's
URL but no logo image — e.g. SimpleFIN). No API key, works for any public
domain; the frontend falls back to a type icon on image-load failure.
"""
from typing import Optional
from urllib.parse import urlparse

from app.core.config import get_settings


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Extract the bare domain from a URL, stripping ``www.`` and scheme.

    Returns None for blanks / unparseable inputs.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def favicon_url_for(website: Optional[str]) -> Optional[str]:
    """Build a favicon-based logo URL for a website, or None if unavailable."""
    domain = extract_domain(website)
    if not domain:
        return None
    size = get_settings().logo_size
    return f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
