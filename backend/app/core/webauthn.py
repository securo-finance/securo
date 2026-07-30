"""Relying-party resolution for WebAuthn.

WebAuthn requires the relying-party ID to be equal to, or a registrable domain
suffix of, the effective domain of the origin the browser is on. A single
configured URL cannot satisfy that for every way a self-hosted deployment is
reached (localhost, a LAN address, a reverse-proxied domain), so the RP ID and
the expected origin are resolved per request instead, from the origin the
browser actually used.

Setting ``WEBAUTHN_RP_ID`` pins the RP ID; requests from an origin outside that
domain are then rejected rather than silently issuing credentials the browser
will refuse.
"""

from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


@dataclass(frozen=True)
class WebAuthnContext:
    """The relying-party ID and browser origin a ceremony is bound to."""

    rp_id: str
    origin: str


def _error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


def is_ip_literal(hostname: str) -> bool:
    try:
        ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return True


def _is_secure_origin(scheme: str, hostname: str) -> bool:
    # Browsers treat http://localhost as a secure context; every other plain-http
    # origin fails the secure-context check before WebAuthn is even reached.
    if scheme == "https":
        return True
    return scheme == "http" and (hostname == "localhost" or hostname.endswith(".localhost"))


def _is_registrable_suffix(hostname: str, rp_id: str) -> bool:
    return hostname == rp_id or hostname.endswith(f".{rp_id}")


def _split_origin(origin: str) -> tuple[str, str, int | None]:
    """Split an origin into (scheme, hostname, port) without pulling in urlparse quirks."""
    scheme, separator, remainder = origin.partition("://")
    if not separator or not remainder:
        raise _error("passkey_origin_invalid", "The request origin could not be understood.")

    scheme = scheme.lower()
    host = remainder.split("/", 1)[0]

    if host.startswith("["):  # IPv6 literal, e.g. [::1]:3000
        closing = host.find("]")
        if closing == -1:
            raise _error("passkey_origin_invalid", "The request origin could not be understood.")
        hostname = host[: closing + 1]
        port_part = host[closing + 1 :].lstrip(":")
    else:
        hostname, _, port_part = host.partition(":")

    if not hostname:
        raise _error("passkey_origin_invalid", "The request origin could not be understood.")

    try:
        port = int(port_part) if port_part else None
    except ValueError:
        raise _error("passkey_origin_invalid", "The request origin could not be understood.") from None

    return scheme, hostname.lower(), port


def _request_origin(request: Request) -> str:
    """The origin the browser is on, from the Origin header or the proxied Host."""
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return origin

    # Non-browser clients and some proxies drop Origin; fall back to the host the
    # request was addressed to, honouring the proxy's forwarded scheme.
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        raise _error(
            "passkey_origin_missing",
            "The request did not include an origin, so the passkey domain could not be determined.",
        )
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    scheme = forwarded_proto or request.url.scheme
    return f"{scheme}://{host}"


def resolve_webauthn_context(request: Request) -> WebAuthnContext:
    """Resolve the RP ID and expected origin for a passkey ceremony.

    Raises a 400 with a machine-readable ``code`` when the origin can never
    support passkeys, so the UI can explain why instead of surfacing an opaque
    browser ``SecurityError``.
    """
    settings = get_settings()
    origin = _request_origin(request)
    scheme, hostname, _ = _split_origin(origin)

    if is_ip_literal(hostname):
        raise _error(
            "passkey_origin_ip",
            "Passkeys cannot be used on an IP address. Open the app on a domain name over HTTPS, "
            "or on http://localhost.",
        )

    if not _is_secure_origin(scheme, hostname):
        raise _error(
            "passkey_origin_insecure",
            "Passkeys require a secure connection. Open the app over HTTPS, or on http://localhost.",
        )

    configured_rp_id = settings.webauthn_rp_id.strip().lower()
    if configured_rp_id:
        if not _is_registrable_suffix(hostname, configured_rp_id):
            raise _error(
                "passkey_origin_mismatch",
                f"This app is reached at '{hostname}', which is outside the configured passkey domain "
                f"'{configured_rp_id}'. Update WEBAUTHN_RP_ID or open the app on that domain.",
            )
        rp_id = configured_rp_id
    else:
        rp_id = hostname

    return WebAuthnContext(rp_id=rp_id, origin=settings.webauthn_origin.strip() or origin)
