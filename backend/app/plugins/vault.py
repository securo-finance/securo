"""Sealing what a plugin connection keeps secret.

The secrets of one connection (an API key, an OAuth token, a certificate and
its password) are one JSON object, encrypted with a key derived from
SECRET_KEY for this purpose only. They are opened just before a call to that
connection's plugin, sent in the call's body, and never returned by the API.
Rotating SECRET_KEY makes them unreadable: the connection then asks to be set
up again, as bank connections do.
"""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.fernet_keys import plugin_secrets_fernet


class SealBroken(Exception):
    """The secrets can't be opened (SECRET_KEY changed, or the data is damaged)."""


def fernet_for(secret_key: str) -> Fernet:
    return plugin_secrets_fernet(secret_key)


def _fernet() -> MultiFernet:
    # Seals with the current key; opens with it or a key the instance used
    # before (see Settings.secret_key_fallbacks).
    settings = get_settings()
    keys = [settings.secret_key.get_secret_value(), *settings.secret_key_fallbacks]
    # An empty legacy key is kept: installs that ran with SECRET_KEY="" sealed with it.
    return MultiFernet([fernet_for(k) for k in dict.fromkeys(keys)])


def seal(secrets: dict[str, Any]) -> str | None:
    if not secrets:
        return None
    return _fernet().encrypt(json.dumps(secrets).encode()).decode()


def unseal(sealed: str | None) -> dict[str, Any]:
    if not sealed:
        return {}
    try:
        return json.loads(_fernet().decrypt(sealed.encode()))
    except (InvalidToken, ValueError) as exc:
        raise SealBroken from exc
