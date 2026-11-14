"""Whether the bank's user (the PSU, in PSD2 terms) is present for a sync.

PSD2 banks treat a data request differently depending on whether the account
holder is online. Enable Banking marks a request as user-present when it
carries the user's own PSU headers (Psu-Ip-Address, Psu-User-Agent, ...), and
as a background request when it carries none; banks cap background requests
(commonly four a day per endpoint) and some only answer certain endpoints
while the user is present.

Only an HTTP request from the user may open a PSU context: the API layer
wraps the manual sync and the OAuth callback in `psu_present`, built from the
request itself. Scheduled syncs never do, so they send no PSU headers.
Values are only ever copied from the user's request, never invented.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from app.core.config import get_settings
from app.core.rate_limit import resolve_client_ip

# Long enough for any real browser header, short enough not to forward junk.
_MAX_HEADER_LENGTH = 512


@dataclass(frozen=True)
class PsuContext:
    """The user's own request details, keyed by Enable Banking's PSU header names."""

    headers: dict[str, str]


_current: ContextVar[Optional[PsuContext]] = ContextVar("psu_context", default=None)


@contextmanager
def psu_present(context: Optional[PsuContext]) -> Generator[None]:
    """Mark the provider calls made inside the block as made with the user present."""
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)


def current_psu_context() -> Optional[PsuContext]:
    """The PSU context of the running request, or None for a background sync."""
    return _current.get()


def _public_ip(value: Optional[str]) -> Optional[str]:
    """`value` if it is a public IP address, else None.

    A private, loopback or link-local address is not the user's address as the
    bank would see it: behind the bundled nginx it is the proxy's container
    address unless TRUSTED_PROXY_HOPS is set. Sending it would tell the bank
    something false, so it is dropped instead.
    """
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    return value if address.is_global else None


def _header(request: Request, name: str) -> Optional[str]:
    """The request header `name`, or None when it cannot be forwarded as is.

    Starlette decodes header bytes as latin-1 and httpx encodes str header
    values as ASCII, so a value outside printable ASCII would make the bank
    request fail. Such a value is left out rather than rewritten: the PSU
    headers describe the user's request, and an altered one would not.
    """
    value = (request.headers.get(name) or "").strip()
    if not value or len(value) > _MAX_HEADER_LENGTH:
        return None
    if not (value.isascii() and value.isprintable()):
        return None
    return value


# PSU header -> the user's request header it is copied from.
_FORWARDED_HEADERS = {
    "Psu-User-Agent": "user-agent",
    "Psu-Referer": "referer",
    "Psu-Accept": "accept",
    "Psu-Accept-Charset": "accept-charset",
    "Psu-Accept-Encoding": "accept-encoding",
    "Psu-Accept-Language": "accept-language",
}


def psu_context_from_request(request: Request) -> PsuContext:
    """Collect the PSU headers this request can honestly supply.

    The client IP comes from the same trusted-proxy rule the login rate limiter
    uses (`TRUSTED_PROXY_HOPS`): X-Forwarded-For is only read as far as the
    configured number of proxies, so a client cannot choose the address sent
    to its bank. Psu-Geo-Location is never sent; Securo does not know it.
    """
    headers: dict[str, str] = {}
    ip = _public_ip(resolve_client_ip(request, get_settings().trusted_proxy_hops))
    if ip:
        headers["Psu-Ip-Address"] = ip
    for psu_name, request_name in _FORWARDED_HEADERS.items():
        value = _header(request, request_name)
        if value:
            headers[psu_name] = value
    return PsuContext(headers=headers)
