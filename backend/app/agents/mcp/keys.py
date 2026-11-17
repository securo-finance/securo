"""The key that signs MCP JWTs, resolved the same way on both sides.

The agent runtime mints tokens with it (`app/agents/mcp/auth.py`) and the
MCP server verifies them with it (`mcp_server/auth.py`), so the two must
derive the same value from the same configuration.

`AGENTS_MCP_JWT_SECRET` wins when it is set. When it is not, the key is
derived from `SECRET_KEY` with HMAC-SHA256, so an install that already
generated a random app secret (install.sh does) needs no extra
configuration.

Both paths refuse a known public default. The key is the only thing
between a caller and a user's financial data: a default published in this
repository lets anyone mint a token for any user whose UUID they know,
and the user directory hands UUIDs to every registered user (issue #1174).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from app.agents.config import get_agent_settings
from app.core.config import get_settings

# Label that separates the derived key from the app secret it comes from.
# Changing it rotates every MCP token, so it stays fixed.
_DERIVATION_LABEL = b"securo:mcp-jwt:v1"

# Values that shipped as defaults in the code, both Compose files and the
# Helm chart. A deployment may still pass one of them explicitly (an old
# .env, a compose override), so they are refused by name.
PUBLIC_DEFAULT_MCP_SECRETS = frozenset(
    {
        "change-me-in-production",
        "dev-mcp-secret-change-in-production",
    }
)
# `SECRET_KEY` defaults, which make any key derived from them public too.
PUBLIC_DEFAULT_APP_SECRETS = frozenset(
    {
        "change-me-in-production",
        "dev-secret-change-in-production",
    }
)

# A derived key is only as secret as its source. `SECRET_KEY` shorter than
# this is refused instead of yielding a weak signing key.
MIN_APP_SECRET_LENGTH = 32

_SETUP_HINT = (
    "Set AGENTS_MCP_JWT_SECRET to a random value (`openssl rand -hex 32`), "
    "or leave it empty and set a random SECRET_KEY with at least 32 characters."
)


class UnsafeMCPSecret(RuntimeError):
    """The MCP signing key is missing, public, or derived from a public value."""


def resolve_mcp_jwt_secret() -> str:
    """Return the key used to sign and verify MCP JWTs.

    Raises `UnsafeMCPSecret` when no safe key can be resolved. Callers that
    serve requests must let it fail startup rather than sign or accept a
    token anyone could forge.
    """
    configured = (get_agent_settings().mcp_jwt_secret or "").strip()
    if configured in PUBLIC_DEFAULT_MCP_SECRETS:
        # Old Compose files and Helm values still pass this. Refusing to start
        # would stop those installs on update, so the value is ignored and the
        # key is derived from SECRET_KEY, which is never public (#1173).
        logging.getLogger(__name__).warning(
            "AGENTS_MCP_JWT_SECRET is set to a public default; ignoring it and deriving "
            "the MCP signing key from SECRET_KEY. Remove it, or %s", _SETUP_HINT[0].lower() + _SETUP_HINT[1:],
        )
        configured = ""
    if configured:
        return configured

    app_secret = get_settings().secret_key.get_secret_value().strip()
    if app_secret in PUBLIC_DEFAULT_APP_SECRETS:
        raise UnsafeMCPSecret(
            "MCP signing key is not configured and SECRET_KEY is a public "
            f"default, so the derived key would be public too. {_SETUP_HINT}"
        )
    if len(app_secret) < MIN_APP_SECRET_LENGTH:
        raise UnsafeMCPSecret(
            "MCP signing key is not configured and SECRET_KEY is shorter than "
            f"{MIN_APP_SECRET_LENGTH} characters, too weak to derive it from. "
            f"{_SETUP_HINT}"
        )
    return hmac.new(app_secret.encode("utf-8"), _DERIVATION_LABEL, hashlib.sha256).hexdigest()


def retired_public_keys() -> list[str]:
    """Keys that signed MCP tokens on installs still on a public default.

    Never used to accept a token: only to tell its holder why it stopped
    working (see mcp_server.auth.verify_request).
    """
    keys = list(PUBLIC_DEFAULT_MCP_SECRETS)
    keys += [
        hmac.new(secret.encode("utf-8"), _DERIVATION_LABEL, hashlib.sha256).hexdigest()
        for secret in PUBLIC_DEFAULT_APP_SECRETS
    ]
    return keys
