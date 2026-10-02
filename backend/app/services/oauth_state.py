"""OAuth state store backed by Redis.

When an OAuth-redirect provider (e.g. Enable Banking) hands the browser
off to a bank consent page, the browser comes back with `?code=...&state=...`.
We need to know which workspace/user/provider that flow belonged to and
what flow params (country, institution) were chosen. Stash that here,
keyed by an unguessable token, and consume it exactly once on callback.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.redis import get_redis

STATE_KEY_PREFIX = "oauth_state:"
STATE_TTL_SECONDS = 600  # 10 minutes


async def store_state(payload: dict[str, Any]) -> str:
    """Persist OAuth flow context and return an opaque state token.

    The caller embeds the token in the OAuth `state` query param; the
    provider echoes it back on redirect.
    """
    state = secrets.token_urlsafe(32)
    body = dict(payload)
    body.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    redis = await get_redis()
    await redis.set(f"{STATE_KEY_PREFIX}{state}", json.dumps(body), ex=STATE_TTL_SECONDS)
    return state


async def alias_state(canonical: str, alias: str) -> None:
    """Copy an existing state payload under a second key.

    Wealth Reader's callback documents ``?nonce=&code=`` and may omit
    ``state``. The provider stashes PKCE under both tokens and aliases
    the Securo state so ``consume_state(nonce)`` still resolves the
    workspace/user/provider payload. Both keys share one consumable
    token: consuming either deletes the sibling.
    """
    if not canonical or not alias or canonical == alias:
        return
    redis = await get_redis()
    raw = await redis.get(f"{STATE_KEY_PREFIX}{canonical}")
    if raw is None:
        return
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return
    siblings = {canonical, alias, *body.get("_oauth_siblings", [])}
    body["_oauth_siblings"] = sorted(siblings)
    blob = json.dumps(body)
    for key in siblings:
        await redis.set(f"{STATE_KEY_PREFIX}{key}", blob, ex=STATE_TTL_SECONDS)


async def consume_state(state: str) -> Optional[dict[str, Any]]:
    """One-shot retrieval — deletes this key and any sibling aliases."""
    if not state:
        return None
    redis = await get_redis()
    key = f"{STATE_KEY_PREFIX}{state}"
    raw = await redis.getdel(key)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    for sibling in payload.pop("_oauth_siblings", []):
        if sibling and sibling != state:
            await redis.delete(f"{STATE_KEY_PREFIX}{sibling}")
    return payload
