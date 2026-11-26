"""Shared cache and instance-wide throttle for calls to the market-data source.

Every workspace of an instance reaches Yahoo from the same address, so a
burst of lookups from one of them spends the budget of all of them: once
Yahoo answers 429, quotes and searches fail for everyone for minutes.

Two things keep that from happening:

* A Redis cache keyed by the public symbol (or search text) only, never by
  workspace or user, so the same ticker asked by two workspaces inside the
  cache window costs one upstream call. Prices live shorter than names and
  search results.
* A fixed one-second window counted in Redis that holds calls under
  ``MAX_CALLS_PER_SECOND`` for the whole instance (every worker process),
  plus a short cooldown after Yahoo rate-limits us, so we stop calling
  while it is still refusing.

Redis being unreachable never blocks a lookup: the cache is skipped and the
throttle lets the call through, which is how the app behaved before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional

logger = logging.getLogger(__name__)

PRICE_TTL_SECONDS = 15 * 60
INFO_TTL_SECONDS = 24 * 60 * 60
SEARCH_TTL_SECONDS = 60 * 60

# Yahoo started refusing at ~23 calls/s from one address; stay well under.
MAX_CALLS_PER_SECOND = 4
# How long a caller waits for a free slot before giving up with a 429.
MAX_WAIT_SECONDS = 3.0
# After Yahoo rate-limits us, every caller fails fast for this long.
COOLDOWN_SECONDS = 60

_PREFIX = "market:yahoo"
_COOLDOWN_KEY = f"{_PREFIX}:cooldown"


async def _redis() -> Optional[Any]:
    try:
        from app.core.redis import get_redis

        return await get_redis()
    except Exception:
        return None


def cache_key(kind: str, key: str) -> str:
    return f"{_PREFIX}:{kind}:{key}"


async def read(kind: str, key: str) -> Any:
    """The cached JSON value for ``kind:key``, or ``None`` on a miss."""
    redis = await _redis()
    if redis is None:
        return None
    full_key = cache_key(kind, key)
    try:
        hit = await redis.get(full_key)
        if isinstance(hit, (str, bytes)):
            return json.loads(hit)
    except Exception:
        logger.debug("market cache read failed for %s", full_key, exc_info=True)
    return None


async def write(kind: str, key: str, value: Any, ttl: int) -> None:
    """Keep ``value`` under ``kind:key`` for ``ttl`` seconds (``None`` is not kept)."""
    if value is None:
        return
    redis = await _redis()
    if redis is None:
        return
    full_key = cache_key(kind, key)
    try:
        await redis.set(full_key, json.dumps(value), ex=ttl)
    except Exception:
        logger.debug("market cache write failed for %s", full_key, exc_info=True)


async def cached(
    kind: str, key: str, ttl: int, fetch: Callable[[], Awaitable[Any]]
) -> Any:
    """Return the cached JSON value for ``kind:key``, else ``fetch()`` and keep it.

    ``None`` from ``fetch`` means "no answer" and is not cached.
    """
    hit = await read(kind, key)
    if hit is not None:
        return hit
    value = await fetch()
    await write(kind, key, value, ttl)
    return value


async def cooling_down() -> bool:
    """True while Yahoo's last 429 still holds every caller back."""
    redis = await _redis()
    if redis is None:
        return False
    try:
        return bool(await redis.get(_COOLDOWN_KEY))
    except Exception:
        logger.debug("market cooldown read failed", exc_info=True)
        return False


async def take_call_slot() -> bool:
    """Reserve one upstream call for this instance; False when none is free.

    Waits up to ``MAX_WAIT_SECONDS`` for the next one-second window, so a
    sequential import keeps going instead of failing on its fifth ticker.
    """
    redis = await _redis()
    if redis is None:
        return True
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        try:
            if await redis.get(_COOLDOWN_KEY):
                return False
            now = time.time()
            window = f"{_PREFIX}:calls:{int(now)}"
            pipe = redis.pipeline()
            pipe.incr(window)
            pipe.expire(window, 2)
            count = int((await pipe.execute())[0])
        except Exception:
            logger.debug("market call throttle unavailable", exc_info=True)
            return True
        if count <= MAX_CALLS_PER_SECOND:
            return True
        wait = 1 - (now % 1)
        if time.monotonic() + wait > deadline:
            return False
        await asyncio.sleep(wait)


async def start_cooldown() -> None:
    """Yahoo just rate-limited us: stop calling it for ``COOLDOWN_SECONDS``."""
    redis = await _redis()
    if redis is None:
        return
    try:
        await redis.set(_COOLDOWN_KEY, "1", ex=COOLDOWN_SECONDS)
    except Exception:
        logger.debug("market cooldown write failed", exc_info=True)
