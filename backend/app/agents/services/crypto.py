"""Symmetric encryption for stored secrets (LLM API keys, bank credentials,
provider settings).

Key derivation: PBKDF2(SHA256, app SECRET_KEY) so we don't need a separate
key file. Values are encrypted with the current key and opened with it or
with a key the instance used before (see `Settings.secret_key_fallbacks`).
Changing SECRET_KEY by hand to an unrelated value still makes existing
ciphertexts unreadable: that means re-entering those secrets.
"""
from __future__ import annotations

import functools

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.fernet_keys import credentials_fernet


def fernet_for(secret_key: str) -> Fernet:
    return credentials_fernet(secret_key)


def _keys() -> tuple[str, ...]:
    settings = get_settings()
    return (settings.secret_key.get_secret_value(), *settings.secret_key_fallbacks)


@functools.lru_cache(maxsize=4)
def _fernet(keys: tuple[str, ...] = ()) -> MultiFernet:
    keys = keys or _keys()
    # Encrypts with the first key; opens with any of them, an empty legacy
    # key included.
    return MultiFernet([fernet_for(k) for k in dict.fromkeys(keys)])


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet(_keys()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet(_keys()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None  # silently treat corrupt/rotated entries as missing
