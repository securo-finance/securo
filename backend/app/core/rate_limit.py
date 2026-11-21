import ipaddress
import time
from collections.abc import Callable
from uuid import uuid4

from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from app.core.config import (
    DEFAULT_TRUSTED_PROXY_NETWORKS,
    get_settings,
    parse_trusted_proxy_networks,
)
from app.core.redis import get_redis


def _is_trusted(address: str, trusted_proxy_networks: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in parse_trusted_proxy_networks(trusted_proxy_networks))


def resolve_client_ip(
    request: Request,
    trusted_proxy_hops: int,
    trusted_proxy_networks: str = DEFAULT_TRUSTED_PROXY_NETWORKS,
) -> str:
    """Client IP for rate-limit keys, honoring up to `trusted_proxy_hops` reverse proxies.

    Each proxy appends the peer it saw to the right end of X-Forwarded-For, so
    the chain is read from the right, starting at the socket peer. A hop is
    only crossed while the address in hand is a trusted proxy (one of
    `trusted_proxy_networks`, the private and loopback ranges by default): an
    address outside them is the client itself, whatever it wrote in the header. That
    keeps a caller who reaches the backend port, or nginx past an outer
    proxy, from picking its own rate-limit bucket (ADR 0024).

    A chain shorter than the hops still to cross means the trust setting does
    not match what reached us, so it is not trusted either.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return "unknown"
    if trusted_proxy_hops <= 0:
        return peer

    forwarded_for = request.headers.get("x-forwarded-for")
    hops = [hop.strip() for hop in forwarded_for.split(",")] if forwarded_for else []

    address = peer
    for crossed in range(1, trusted_proxy_hops + 1):
        if not _is_trusted(address, trusted_proxy_networks):
            return address
        if len(hops) < crossed:
            return "unknown"
        address = hops[-crossed]
    return address


class RateLimiter:
    """Sliding window per client IP, counted in Redis.

    `max_requests` may be a zero-argument callable, so a ceiling kept in
    settings is read on every request instead of frozen at import time. A
    ceiling of zero or less turns the limiter off.

    Requests are keyed by `scope`, or by the request path when there is none.
    A scope shared by several routes, or by every id a path template takes,
    puts them in one bucket per IP, so moving to another route or another id
    does not hand out a fresh budget.

    `unavailable_status`, when set, answers that status when Redis cannot be
    reached. Left unset the driver error propagates as it always did, which
    keeps the response of the routes that were limiting before this option
    existed unchanged.
    """

    def __init__(
        self,
        max_requests: int | Callable[[], int],
        window_seconds: int,
        scope: str | None = None,
        unavailable_status: int | None = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.scope = scope
        self.unavailable_status = unavailable_status

    def ceiling(self) -> int:
        limit = self.max_requests
        if isinstance(limit, int):
            return limit
        return limit()

    async def __call__(self, request: Request) -> None:
        limit = self.ceiling()
        if limit <= 0:
            return

        settings = get_settings()
        client_ip = resolve_client_ip(
            request, settings.trusted_proxy_hops, settings.trusted_proxy_networks
        )
        key = f"rate_limit:{self.scope or request.url.path}:{client_ip}"

        try:
            r = await get_redis()
            now = time.time()
            window_start = now - self.window_seconds

            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            # A unique member per request: keyed by the timestamp, two requests
            # that share a float value would land on the same member and the
            # window would count them as one.
            pipe.zadd(key, {uuid4().hex: now})
            pipe.expire(key, self.window_seconds)
            results = await pipe.execute()
        except RedisError as exc:
            # The window lives in Redis. With it unreachable nothing can be
            # counted, and failing open would drop the protection exactly when
            # the instance is already unhealthy. Where the caller opted in, a
            # 503 says "try again" instead of turning the route into a bare 500;
            # the routes that were limiting before this option keep their
            # previous behavior, so auth responses do not change here.
            if self.unavailable_status is None:
                raise
            raise HTTPException(
                status_code=self.unavailable_status,
                detail="Rate limit unavailable",
            ) from exc

        request_count = results[1]

        if request_count >= limit:
            retry_after = self.window_seconds
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )


login_rate_limit = RateLimiter(max_requests=5, window_seconds=60)
register_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)
password_reset_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)


def _agents_chat_ceiling() -> int:
    return get_settings().agents_chat_rate_limit_per_minute


# Every assistant message spends provider tokens, so the chat has a ceiling of
# its own, read from settings so a demo can be tighter than a self-hosted
# instance. All agents share one bucket: per-path keys would give a caller a
# fresh minute every time it switched agent id.
agents_chat_rate_limit = RateLimiter(
    max_requests=_agents_chat_ceiling,
    window_seconds=60,
    scope="agents_chat",
    unavailable_status=503,
)
