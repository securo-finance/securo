"""Signed passes for the egress proxy.

The runner hands each plugin subprocess a pass naming the plugin, the hosts it
may reach and when the pass expires, signed with a secret it shares with the
proxy. The proxy needs no state and no call back to the runner: it checks the
signature and the expiry, then the host. A pass leaked from one call is only
good for that plugin's own hosts, and only until the call's timeout.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

#: `host` or `host:port`, lowercase. A pass never carries a wildcard.
HOST_PATTERN = re.compile(r"^(?=.{1,253}(:|$))([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(:[0-9]{1,5})?$")


class InvalidPass(Exception):
    pass


@dataclass(frozen=True)
class EgressPass:
    plugin: str
    hosts: frozenset[str]
    """`host:port` entries; a host declared without a port means 443."""
    expires: int

    def allows(self, host: str, port: int) -> bool:
        return f"{host.lower().rstrip('.')}:{port}" in self.hosts


def normalize_host(entry: str) -> str:
    """`api.example.com` as `api.example.com:443`; refuses anything that isn't a plain host."""
    entry = entry.strip().lower()
    if not HOST_PATTERN.match(entry):
        raise ValueError(f"not a host name: {entry!r}")
    host, _, port = entry.partition(":")
    port_number = int(port) if port else 443
    if not 0 < port_number < 65536:
        raise ValueError(f"not a port: {port}")
    return f"{host}:{port_number}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def mint(secret: str, plugin: str, hosts: list[str], ttl: int, *, now: float | None = None) -> str:
    if not secret:
        raise ValueError("the egress secret is not set")
    payload = {
        "p": plugin,
        "h": sorted({normalize_host(h) for h in hosts}),
        "e": int((now if now is not None else time.time()) + ttl),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(secret, body)}"


def verify(secret: str, token: str, *, now: float | None = None) -> EgressPass:
    if not secret:
        raise InvalidPass("the egress secret is not set")
    body, _, signature = token.partition(".")
    if not body.isascii() or not signature.isascii():
        raise InvalidPass("malformed pass")
    if not body or not signature or not hmac.compare_digest(signature.encode(), _sign(secret, body).encode()):
        raise InvalidPass("bad signature")
    try:
        payload = json.loads(_unb64(body))
        granted = EgressPass(str(payload["p"]), frozenset(map(str, payload["h"])), int(payload["e"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidPass("malformed pass") from exc
    if granted.expires < (now if now is not None else time.time()):
        raise InvalidPass("expired")
    return granted
