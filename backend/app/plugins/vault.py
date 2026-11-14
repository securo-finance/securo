"""Sealing what a plugin connection keeps secret.

The secrets of one connection (an API key, an OAuth token, a certificate and
its password) are one JSON object, encrypted with a key derived from
SECRET_KEY for this purpose only. They are opened just before a call to that
connection's plugin, sent in the call's body, and never returned by the API.
Rotating SECRET_KEY makes them unreadable: the connection then asks to be set
up again, as bank connections do.
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_SALT = b"securo-plugin-secrets-v1"


class SealBroken(Exception):
    """The secrets can't be opened (SECRET_KEY changed, or the data is damaged)."""


@lru_cache(maxsize=4)
def _fernet(secret_key: str) -> Fernet:
    key = hashlib.pbkdf2_hmac("sha256", secret_key.encode(), _SALT, 100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def seal(secrets: dict[str, Any]) -> str | None:
    if not secrets:
        return None
    return _fernet(get_settings().secret_key.get_secret_value()).encrypt(json.dumps(secrets).encode()).decode()


def unseal(sealed: str | None) -> dict[str, Any]:
    if not sealed:
        return {}
    try:
        return json.loads(_fernet(get_settings().secret_key.get_secret_value()).decrypt(sealed.encode()))
    except (InvalidToken, ValueError) as exc:
        raise SealBroken from exc
