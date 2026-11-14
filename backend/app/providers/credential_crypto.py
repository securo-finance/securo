"""Encryption for bank login details stored on a connection.

Same scheme as the agents' LLM keys (Fernet, key derived from SECRET_KEY by
PBKDF2) with its own salt, so the two kinds of secret never share a key.
Rotating SECRET_KEY makes stored logins unreadable; the connection then asks
the user to reconnect.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_SALT = b"securo-bank-credentials-v1"


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    secret = get_settings().secret_key.get_secret_value().encode("utf-8")
    raw = hashlib.pbkdf2_hmac("sha256", secret, _SALT, iterations=100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


def seal(values: dict[str, str]) -> str:
    return _fernet().encrypt(json.dumps(values).encode("utf-8")).decode("ascii")


def unseal(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    try:
        return json.loads(_fernet().decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError):
        return None
