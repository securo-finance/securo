"""Fernet keys derived from SECRET_KEY, one per purpose.

Kept free of app imports: the instance key is resolved while settings first
load, before the models and services can be imported, and moving saved
credentials to it needs these derivations (app.core.instance_secrets).
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

# LLM API keys, bank connection credentials and provider settings.
CREDENTIALS_SALT = b"securo-agents-llm-keys-v1"
# Plugin connection secrets.
PLUGIN_SECRETS_SALT = b"securo-plugin-secrets-v1"


@lru_cache(maxsize=16)
def derive(secret_key: str, salt: bytes) -> Fernet:
    raw = hashlib.pbkdf2_hmac("sha256", secret_key.encode("utf-8"), salt, iterations=100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


def credentials_fernet(secret_key: str) -> Fernet:
    return derive(secret_key, CREDENTIALS_SALT)


def plugin_secrets_fernet(secret_key: str) -> Fernet:
    return derive(secret_key, PLUGIN_SECRETS_SALT)
