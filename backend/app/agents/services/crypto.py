"""Symmetric encryption for stored secrets (LLM API keys, etc.).

Key derivation: PBKDF2(SHA256, app SECRET_KEY) so we don't need a
separate key file. If the operator rotates SECRET_KEY, existing
ciphertexts become unreadable — that's by design (rotation = re-enter
your provider keys).
"""
from __future__ import annotations

import base64
import functools
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


_SALT = b"securo-agents-llm-keys-v1"


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    secret = get_settings().secret_key.get_secret_value().encode("utf-8")
    raw = hashlib.pbkdf2_hmac("sha256", secret, _SALT, iterations=100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None  # silently treat corrupt/rotated entries as missing
